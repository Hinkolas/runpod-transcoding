"""RunPod serverless worker: transcode a source video into HLS renditions.

The whole job is driven by the request `input` (see schema.py): the quality
renditions, segment length and threading are request-configurable, and the
download/upload locations are pluggable per request (s3 or http).

CPU-optimised: each requested rendition is encoded in its own ffmpeg process and
the renditions run in parallel, so a many-core box (e.g. 32 vCPU) is saturated far
better than relying on a single libx264 encode's internal threading.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import runpod
from pydantic import ValidationError

import transcode
from schema import Rendition, TranscodeInput
from storage import make_destination, make_source


def progress(job: dict[str, Any], phase: str, percent: int | None = None) -> None:
    """Send a structured progress update to RunPod (best-effort).

    The payload is JSON so a polling client can render a phase label and, while
    encoding, a percentage. It surfaces in the `/status` response's `output`
    field while the job is IN_PROGRESS; clients that only read the terminal
    result are unaffected. If RunPod isn't available (e.g. local test run) we
    just log it.
    """
    update: dict[str, Any] = {"phase": phase}
    if percent is not None:
        update["percent"] = percent
    try:
        runpod.serverless.progress_update(job, json.dumps(update))
    except Exception:
        print(json.dumps(update), flush=True)


class EncodeProgress:
    """Folds the parallel renditions' ffmpeg progress into one throttled percent.

    Each rendition reports its own 0..1 fraction from its encoder thread; the
    overall percent is their mean (every rendition decodes the full source, so
    equal weight is right). Updates are throttled to at most ~1/s and only when
    the integer percent advances, to avoid hammering RunPod's API.
    """

    def __init__(self, job: dict[str, Any], count: int) -> None:
        self._job = job
        self._fractions = [0.0] * count
        self._lock = threading.Lock()
        self._last_emit = 0.0
        self._last_percent = -1

    def report(self, index: int, fraction: float) -> None:
        with self._lock:
            self._fractions[index] = fraction
            percent = int(sum(self._fractions) / len(self._fractions) * 100)
            now = time.time()
            if percent == self._last_percent or (percent < 100 and now - self._last_emit < 1.0):
                return
            self._last_percent = percent
            self._last_emit = now
        progress(self._job, "encoding", percent)


def handle_transcode(job: dict[str, Any]) -> dict[str, Any]:
    started_at = time.time()
    try:
        payload = TranscodeInput.model_validate(job.get("input") or {})
    except ValidationError as error:
        raise ValueError(error.json()) from error

    source = make_source(payload.source)
    destination = make_destination(payload.destination)

    with tempfile.TemporaryDirectory(prefix="runpod-hls-") as tmp:
        temp_dir = Path(tmp)
        source_path = temp_dir / "source"
        output_dir = temp_dir / "hls"
        output_dir.mkdir()

        progress(job, "downloading")
        source.download(source_path)

        progress(job, "probing")
        probe = transcode.probe_video(source_path)
        source_height = int(probe["height"])

        selected = [
            r for r in payload.renditions if payload.allowUpscale or r.height <= source_height
        ]
        if not selected:
            selected = [min(payload.renditions, key=lambda item: item.height)]

        max_workers, per_job_threads = transcode.plan_concurrency(len(selected), payload.threads)
        print(
            f"Transcoding {len(selected)} renditions "
            f"({max_workers} parallel × {per_job_threads} threads)",
            flush=True,
        )
        progress(job, "encoding", 0)
        encode_progress = EncodeProgress(job, len(selected))

        poster_path = transcode.extract_poster(
            source_path, output_dir, float(probe["durationSeconds"])
        )

        def encode(index: int, rendition: Rendition) -> dict[str, Any]:
            variant = transcode.transcode_rendition(
                source_path, output_dir, rendition, payload.segmentSeconds, per_job_threads,
                float(probe["durationSeconds"]),
                on_progress=lambda fraction: encode_progress.report(index, fraction),
            )
            variant["width"] = rendition.width or transcode.calculated_width(
                int(probe["width"]), int(probe["height"]), rendition.height
            )
            return variant

        # The storyboard is a full decode pass; run it alongside the encodes (its own
        # pool slot, so it overlaps their wall-clock instead of stealing an encoder).
        want_storyboard = payload.thumbnails is not None
        storyboard: dict[str, Any] | None = None
        pool_size = max_workers + (1 if want_storyboard else 0)
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            encode_futures = [
                pool.submit(encode, index, rendition) for index, rendition in enumerate(selected)
            ]
            storyboard_future = (
                pool.submit(
                    transcode.extract_storyboard,
                    source_path, output_dir, payload.thumbnails,
                    int(probe["width"]), int(probe["height"]),
                    float(probe["durationSeconds"]),
                )
                if want_storyboard
                else None
            )
            variants = [future.result() for future in encode_futures]
            if storyboard_future is not None:
                storyboard = storyboard_future.result()

        transcode.write_master_playlist(output_dir, variants)

        progress(job, "uploading")
        stored = destination.upload(output_dir)

    # Map every uploaded relative path to where it landed (s3 key or http URL),
    # then resolve the well-known outputs against it.
    locations = {item.relativePath: item.location for item in stored}
    master_location = locations.get("master.m3u8")
    poster_location = locations.get("poster.jpg") if poster_path else None
    storyboard_location = locations.get("storyboard.vtt") if storyboard else None
    if storyboard:
        storyboard = {**storyboard, "location": storyboard_location}

    rendition_outputs = [
        {**variant, "location": locations.get(variant["playlistFile"])}
        for variant in variants
    ]

    response: dict[str, Any] = {
        "appVideoId": payload.appVideoId,
        "metadata": payload.metadata,
        "destinationType": payload.destination.type,
        "outputs": [
            {"relativePath": item.relativePath, "location": item.location} for item in stored
        ],
        "renditions": rendition_outputs,
        "storyboard": storyboard,
        "probe": probe,
        "uploadedObjectCount": len(stored),
        "encoder": "libx264/libx265 (cpu)",
        "parallelism": {"workers": max_workers, "threadsPerEncode": per_job_threads},
        "durationMs": int((time.time() - started_at) * 1000),
    }

    # Surface the master playlist and poster under names that match the backend.
    if payload.destination.type == "s3":
        response["masterPlaylistKey"] = master_location
        response["posterKey"] = poster_location
        response["storyboardVttKey"] = storyboard_location
    else:
        response["masterPlaylistUrl"] = master_location
        response["posterUrl"] = poster_location
        response["storyboardVttUrl"] = storyboard_location

    return response


def handler(job: dict[str, Any]) -> dict[str, Any]:
    return handle_transcode(job)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--test_input":
        with open(sys.argv[2], "r", encoding="utf-8") as file:
            test_job = json.load(file)
        print(json.dumps(handler(test_job), indent=2))
    else:
        runpod.serverless.start({"handler": handler})
