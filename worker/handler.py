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
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import runpod
from pydantic import ValidationError

import transcode
from schema import Rendition, TranscodeInput
from storage import make_destination, make_source


def progress(job: dict[str, Any], message: str) -> None:
    try:
        runpod.serverless.progress_update(job, message)
    except Exception:
        print(message, flush=True)


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

        progress(job, "Downloading source video")
        source.download(source_path)

        progress(job, "Probing source video")
        probe = transcode.probe_video(source_path)
        source_height = int(probe["height"])

        selected = [
            r for r in payload.renditions if payload.allowUpscale or r.height <= source_height
        ]
        if not selected:
            selected = [min(payload.renditions, key=lambda item: item.height)]

        max_workers, per_job_threads = transcode.plan_concurrency(len(selected), payload.threads)
        progress(
            job,
            f"Transcoding {len(selected)} renditions "
            f"({max_workers} parallel × {per_job_threads} threads)",
        )

        poster_path = transcode.extract_poster(
            source_path, output_dir, float(probe["durationSeconds"])
        )

        def encode(rendition: Rendition) -> dict[str, Any]:
            variant = transcode.transcode_rendition(
                source_path, output_dir, rendition, payload.segmentSeconds, per_job_threads,
                float(probe["durationSeconds"]),
            )
            variant["width"] = rendition.width or transcode.calculated_width(
                int(probe["width"]), int(probe["height"]), rendition.height
            )
            return variant

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            variants = list(pool.map(encode, selected))

        transcode.write_master_playlist(output_dir, variants)

        progress(job, "Uploading HLS output")
        stored = destination.upload(output_dir)

    # Map every uploaded relative path to where it landed (s3 key or http URL),
    # then resolve the well-known outputs against it.
    locations = {item.relativePath: item.location for item in stored}
    master_location = locations.get("master.m3u8")
    poster_location = locations.get("poster.jpg") if poster_path else None

    rendition_outputs = [
        {**variant, "location": locations.get(variant["playlistFile"])}
        for variant in variants
    ]

    response: dict[str, Any] = {
        "appVideoId": payload.appVideoId,
        "destinationType": payload.destination.type,
        "outputs": [
            {"relativePath": item.relativePath, "location": item.location} for item in stored
        ],
        "renditions": rendition_outputs,
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
    else:
        response["masterPlaylistUrl"] = master_location
        response["posterUrl"] = poster_location

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
