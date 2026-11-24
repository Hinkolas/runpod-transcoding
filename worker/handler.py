"""RunPod serverless worker: transcode a source file into HLS renditions.

The whole job is driven by the request `input` (see schema.py): video renditions,
audio-only renditions, a progressive audio download, waveform peaks, segment
length and threading are request-configurable, and the download/upload locations
are pluggable per request (s3 or http). The worker produces what the source
supports (video outputs need a video stream, audio outputs an audio stream) and
reports the rest as skipped.

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
from typing import Any, Callable

import runpod
from pydantic import ValidationError

import transcode
import waveform
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
    """Folds the parallel encode tasks' ffmpeg progress into one throttled percent.

    Each task reports its own 0..1 fraction from its worker thread; the overall
    percent is their weighted mean. A video rendition weighs 1; an audio task
    (audio rendition, download, waveform, loudness analysis) weighs 0.25, since
    it is far cheaper. Updates are throttled to at most ~1/s and only when the
    integer percent advances, to avoid hammering RunPod's API.
    """

    def __init__(self, job: dict[str, Any], weights: list[float]) -> None:
        self._job = job
        self._weights = weights
        self._total = sum(weights) or 1.0
        self._fractions = [0.0] * len(weights)
        self._lock = threading.Lock()
        self._last_emit = 0.0
        self._last_percent = -1

    def add(self, weight: float) -> int:
        """Register a task and return its index (call before encoding starts)."""
        self._weights.append(weight)
        self._fractions.append(0.0)
        self._total = sum(self._weights)
        return len(self._weights) - 1

    def reporter(self, index: int) -> Callable[[float], None]:
        return lambda fraction: self.report(index, fraction)

    def report(self, index: int, fraction: float) -> None:
        with self._lock:
            self._fractions[index] = fraction
            done = sum(w * f for w, f in zip(self._weights, self._fractions))
            percent = int(done / self._total * 100)
            now = time.time()
            if percent == self._last_percent or (percent < 100 and now - self._last_emit < 1.0):
                return
            self._last_percent = percent
            self._last_emit = now
        progress(self._job, "encoding", percent)


VIDEO_TASK_WEIGHT = 1.0
AUDIO_TASK_WEIGHT = 0.25


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
        probe = transcode.probe(source_path)
        duration = float(probe["durationSeconds"])
        has_video, has_audio = bool(probe["hasVideo"]), bool(probe["hasAudio"])
        video_index, audio_index = probe["videoStreamIndex"], probe["audioStreamIndex"]

        # Decide what the source can produce; report the rest as skipped.
        skipped: list[dict[str, Any]] = []

        def skip(output: str, label: str | None, reason: str) -> None:
            skipped.append({"output": output, "label": label, "reason": reason})

        selected: list[Rendition] = []
        if payload.renditions and has_video:
            source_height = int(probe["height"] or 0)
            selected = [
                r for r in payload.renditions
                if payload.allowUpscale or r.height <= source_height
            ]
            if not selected:
                selected = [min(payload.renditions, key=lambda item: item.height)]
        for rendition in payload.renditions if not has_video else []:
            skip("renditions", rendition.label, "no_video_stream")

        want_storyboard = payload.thumbnails is not None and has_video
        if payload.thumbnails is not None and not has_video:
            skip("thumbnails", None, "no_video_stream")

        audio_renditions = list(payload.audioRenditions) if has_audio else []
        audio_download = payload.audioDownload if has_audio else None
        waveform_settings = payload.waveform if has_audio else None
        if not has_audio:
            for audio_rendition in payload.audioRenditions:
                skip("audioRenditions", audio_rendition.label, "no_audio_stream")
            if payload.audioDownload is not None:
                skip("audioDownload", None, "no_audio_stream")
            if payload.waveform is not None:
                skip("waveform", None, "no_audio_stream")

        # A job whose outputs the source can't support is not an error: the
        # response's probe and `skipped` tell the caller what the source has.
        if not (selected or audio_renditions or audio_download or waveform_settings):
            print("Nothing to encode for this source; every requested output is skipped",
                  flush=True)

        # Loudness only matters for the audio-only outputs it is applied to.
        want_loudness = payload.loudness is not None and bool(audio_renditions or audio_download)

        max_workers, per_job_threads = transcode.plan_concurrency(len(selected), payload.threads)
        # Audio encodes are light: AAC/MP3 are effectively single-threaded.
        audio_threads = 1 if payload.threads == 1 else 2
        print(
            f"Transcoding {len(selected)} video renditions "
            f"({max_workers} parallel × {per_job_threads} threads), "
            f"{len(audio_renditions)} audio renditions, "
            f"download={audio_download.format if audio_download else None}, "
            f"waveform={waveform_settings is not None}, loudness={want_loudness}",
            flush=True,
        )
        progress(job, "encoding", 0)
        encode_progress = EncodeProgress(job, [])

        poster_path: Path | None = None
        poster_source: str | None = None
        if has_video:
            poster_path = transcode.extract_poster(source_path, output_dir, duration, video_index)
            poster_source = "frame"
        elif probe["hasCoverArt"]:
            poster_path = transcode.extract_cover_art(source_path, output_dir)
            poster_source = "coverArt"

        def encode(index: int, rendition: Rendition) -> dict[str, Any]:
            variant = transcode.transcode_rendition(
                source_path, output_dir, rendition, payload.segmentSeconds, per_job_threads,
                duration,
                on_progress=encode_progress.reporter(index),
                video_index=video_index,
                audio_index=audio_index,
            )
            variant["width"] = rendition.width or transcode.calculated_width(
                int(probe["width"] or 0), int(probe["height"] or 0), rendition.height
            )
            return variant

        # Register every task's progress weight up front, so the percent never
        # jumps backwards as later tasks start.
        video_slots = [encode_progress.add(VIDEO_TASK_WEIGHT) for _ in selected]
        loudness_slot = encode_progress.add(AUDIO_TASK_WEIGHT) if want_loudness else None
        audio_slots = [encode_progress.add(AUDIO_TASK_WEIGHT) for _ in audio_renditions]
        download_slot = encode_progress.add(AUDIO_TASK_WEIGHT) if audio_download else None
        waveform_slot = encode_progress.add(AUDIO_TASK_WEIGHT) if waveform_settings else None

        # Every task gets its own pool thread (so a task waiting on the loudness
        # analysis can never starve it). The storyboard is a full decode pass; it
        # runs alongside the encodes instead of stealing an encoder slot.
        pool_size = (
            max(1, len(selected))
            + (1 if want_storyboard else 0)
            + (1 if want_loudness else 0)
            + len(audio_renditions)
            + (1 if audio_download else 0)
            + (1 if waveform_settings else 0)
        )
        storyboard: dict[str, Any] | None = None
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            loudness_future = (
                pool.submit(
                    transcode.analyse_loudness,
                    source_path, audio_index, payload.loudness, duration,
                    encode_progress.reporter(loudness_slot),
                )
                if want_loudness
                else None
            )

            def audio_filter() -> str | None:
                if loudness_future is None:
                    return None
                return transcode.loudnorm_filter(loudness_future.result())

            encode_futures = [
                pool.submit(encode, slot, rendition)
                for slot, rendition in zip(video_slots, selected)
            ]
            audio_futures = [
                pool.submit(
                    lambda rendition=rendition, slot=slot: transcode.transcode_audio_rendition(
                        source_path, output_dir, rendition, payload.segmentSeconds,
                        audio_threads, duration,
                        audio_index=audio_index,
                        audio_filter=audio_filter(),
                        on_progress=encode_progress.reporter(slot),
                    )
                )
                for slot, rendition in zip(audio_slots, audio_renditions)
            ]
            download_future = (
                pool.submit(
                    lambda: transcode.encode_audio_download(
                        source_path, output_dir, audio_download, audio_threads, duration,
                        audio_index=audio_index,
                        audio_filter=audio_filter(),
                        on_progress=encode_progress.reporter(download_slot),
                    )
                )
                if audio_download
                else None
            )
            waveform_future = (
                pool.submit(
                    waveform.generate_waveform,
                    source_path, output_dir, audio_index, waveform_settings, duration,
                    encode_progress.reporter(waveform_slot),
                )
                if waveform_settings
                else None
            )
            storyboard_future = (
                pool.submit(
                    transcode.extract_storyboard,
                    source_path, output_dir, payload.thumbnails,
                    int(probe["width"] or 0), int(probe["height"] or 0),
                    duration, video_index,
                )
                if want_storyboard
                else None
            )
            variants = [future.result() for future in encode_futures]
            loudness = loudness_future.result() if loudness_future else None
            audio_variants = [future.result() for future in audio_futures]
            download = download_future.result() if download_future else None
            waveform_info = waveform_future.result() if waveform_future else None
            if storyboard_future is not None:
                storyboard = storyboard_future.result()

        if variants:
            transcode.write_master_playlist(output_dir, variants)
        if audio_variants:
            transcode.write_audio_master_playlist(output_dir, audio_variants)

        progress(job, "uploading")
        stored = destination.upload(output_dir)

    # Map every uploaded relative path to where it landed (s3 key or http URL),
    # then resolve the well-known outputs against it.
    locations = {item.relativePath: item.location for item in stored}
    master_location = locations.get("master.m3u8") if variants else None
    poster_location = locations.get("poster.jpg") if poster_path else None
    storyboard_location = locations.get("storyboard.vtt") if storyboard else None
    if storyboard:
        storyboard = {**storyboard, "location": storyboard_location}

    rendition_outputs = [
        {**variant, "location": locations.get(variant["playlistFile"])}
        for variant in variants
    ]
    audio_output = (
        {
            "masterPlaylist": "audio.m3u8" if audio_variants else None,
            "renditions": audio_variants,
            "download": download,
        }
        if audio_variants or download
        else None
    )
    loudness_output = (
        {key: loudness[key] for key in ("measuredI", "measuredTP", "measuredLRA", "targetLufs", "truePeak")}
        if loudness
        else None
    )

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
        "audio": audio_output,
        "waveform": waveform_info,
        "poster": {"file": "poster.jpg", "source": poster_source} if poster_path else None,
        "loudness": loudness_output,
        "skipped": skipped,
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
