"""ffmpeg / ffprobe logic.

Pure local-filesystem operations — no storage, no RunPod. Each rendition is
encoded in its own ffmpeg process and the renditions run in parallel (ffmpeg
releases the GIL in a subprocess), so a many-core box is saturated far better
than relying on a single libx264 encode's internal threading.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from schema import Rendition


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
    return result


def probe_video(path: Path) -> dict[str, Any]:
    result = run_command(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    )
    data = json.loads(result.stdout)
    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if not video_stream:
        raise RuntimeError("Source object does not contain a video stream")

    return {
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "durationSeconds": float(
            video_stream.get("duration") or data.get("format", {}).get("duration") or 0
        ),
        "codec": video_stream.get("codec_name"),
        "format": data.get("format", {}).get("format_name"),
        "bitrate": int(data.get("format", {}).get("bit_rate") or 0),
    }


def bitrate_to_int(value: str) -> int:
    normalized = value.strip().lower()
    if normalized.endswith("k"):
        return int(float(normalized[:-1]) * 1000)
    if normalized.endswith("m"):
        return int(float(normalized[:-1]) * 1_000_000)
    return int(float(normalized))


def calculated_width(source_width: int, source_height: int, target_height: int) -> int | None:
    if not source_width or not source_height:
        return None
    scaled = round((source_width / source_height) * target_height)
    return scaled if scaled % 2 == 0 else scaled + 1


def encoder_args(rendition: Rendition) -> list[str]:
    if rendition.codec == "h265":
        # HEVC plays in Safari / some Edge over fMP4. h264 is the universal default.
        return ["-c:v", "libx265", "-preset", rendition.preset, "-tag:v", "hvc1"]
    return ["-c:v", "libx264", "-preset", rendition.preset, "-profile:v", "high", "-level", "4.1"]


def rate_control_args(rendition: Rendition) -> list[str]:
    """CRF (constant quality) or -b:v (ABR), always capped by maxrate/bufsize.

    The schema guarantees exactly one of `crf` / `videoBitrate` is set.
    """
    if rendition.crf is not None:
        rate = ["-crf", str(rendition.crf)]
    else:
        rate = ["-b:v", str(rendition.videoBitrate)]
    return [*rate, "-maxrate", rendition.maxrate, "-bufsize", rendition.bufsize]


def transcode_rendition(
    source: Path,
    output_dir: Path,
    rendition: Rendition,
    segment_seconds: int,
    threads: int,
) -> dict[str, Any]:
    variant_dir = output_dir / rendition.label
    variant_dir.mkdir(parents=True, exist_ok=True)
    playlist_path = variant_dir / "index.m3u8"
    segment_pattern = variant_dir / "segment_%05d.ts"

    command = [
        "ffmpeg", "-y", "-i", str(source),
        "-vf", f"scale=-2:{rendition.height}",
        *encoder_args(rendition),
        "-threads", str(threads),
        "-pix_fmt", rendition.pixelFormat,
        *rate_control_args(rendition),
        # Align GOP boundaries to segment length for clean ABR switching.
        "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})",
        "-sc_threshold", "0",
        "-c:a", rendition.audioCodec,
        "-b:a", rendition.audioBitrate,
        "-ac", str(rendition.audioChannels),
        "-ar", str(rendition.audioSampleRate),
        "-f", "hls",
        "-hls_time", str(segment_seconds),
        "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(segment_pattern),
        str(playlist_path),
    ]
    run_command(command)

    # HLS master playlists require a BANDWIDTH per variant. In CRF mode there is
    # no target bitrate, so advertise the peak (maxrate) as the bandwidth estimate.
    video_bps = bitrate_to_int(rendition.videoBitrate or rendition.maxrate)
    return {
        "label": rendition.label,
        "height": rendition.height,
        "width": rendition.width,
        "crf": rendition.crf,
        "videoBitrate": rendition.videoBitrate,
        "audioBitrate": rendition.audioBitrate,
        "bandwidth": video_bps + bitrate_to_int(rendition.audioBitrate),
        "playlistFile": f"{rendition.label}/index.m3u8",
        "segmentPrefix": rendition.label,
    }


def extract_poster(source: Path, output_dir: Path, duration: float) -> Path | None:
    seek = max(0.0, min(3.0, duration / 2 if duration else 1.0))
    poster_path = output_dir / "poster.jpg"
    try:
        run_command(
            [
                "ffmpeg", "-y", "-ss", f"{seek:.2f}", "-i", str(source),
                "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(poster_path),
            ]
        )
        return poster_path if poster_path.exists() else None
    except Exception as error:  # poster is best-effort; never fail the job for it
        print(f"poster extraction failed: {error}", flush=True)
        return None


def write_master_playlist(output_dir: Path, variants: list[dict[str, Any]]) -> None:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for variant in sorted(variants, key=lambda v: v["bandwidth"]):
        resolution = f',RESOLUTION={variant["width"]}x{variant["height"]}' if variant.get("width") else ""
        lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={variant["bandwidth"]}{resolution}')
        lines.append(variant["playlistFile"])
    (output_dir / "master.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plan_concurrency(num_renditions: int, requested_threads: int) -> tuple[int, int]:
    """Return (max_workers, threads_per_encode) tuned to the core count."""
    cpu = os.cpu_count() or 4
    workers = max(1, num_renditions)
    if requested_threads > 0:
        return workers, requested_threads
    per_job = max(2, cpu // workers)
    return workers, per_job
