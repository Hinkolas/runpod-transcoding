"""ffmpeg / ffprobe logic.

Pure local-filesystem operations — no storage, no RunPod. Each rendition is
encoded in its own ffmpeg process and the renditions run in parallel (ffmpeg
releases the GIL in a subprocess), so a many-core box is saturated far better
than relying on a single libx264 encode's internal threading.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from schema import Rendition, Thumbnails


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
    return result


def run_with_progress(
    command: list[str],
    duration: float,
    on_progress: Callable[[float], None],
) -> None:
    """Run an ffmpeg command, reporting completion as a 0..1 fraction.

    The command must include `-progress pipe:1`; ffmpeg then writes `key=value`
    lines (notably `out_time_us`) to stdout, which we divide by the known source
    `duration`. stderr is drained on a side thread so a chatty encoder can't fill
    the pipe buffer and deadlock the stdout reader.
    """
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert process.stdout is not None and process.stderr is not None

    stderr_tail: list[str] = []

    def drain_stderr() -> None:
        for line in process.stderr:  # type: ignore[union-attr]
            stderr_tail.append(line)

    err_thread = threading.Thread(target=drain_stderr, daemon=True)
    err_thread.start()

    for line in process.stdout:
        if duration > 0 and line.startswith("out_time_us="):
            raw = line[len("out_time_us=") :].strip()
            try:
                on_progress(min(1.0, max(0.0, int(raw) / 1_000_000 / duration)))
            except ValueError:
                pass  # 'N/A' before the first frame is decoded

    process.wait()
    err_thread.join()
    if process.returncode != 0:
        stderr = "".join(stderr_tail)
        raise RuntimeError(
            f"Command failed ({process.returncode}): {' '.join(command)}\n"
            f"stderr: {stderr[-2000:]}"
        )
    on_progress(1.0)


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
    """CRF (constant quality) or -b:v (ABR), with an optional maxrate/bufsize cap.

    The schema guarantees exactly one of `crf` / `videoBitrate` is set, and that
    `maxrate`/`bufsize` are either both present or both absent.
    """
    if rendition.crf is not None:
        rate = ["-crf", str(rendition.crf)]
    else:
        rate = ["-b:v", str(rendition.videoBitrate)]
    if rendition.maxrate and rendition.bufsize:
        rate += ["-maxrate", rendition.maxrate, "-bufsize", rendition.bufsize]
    return rate


def transcode_rendition(
    source: Path,
    output_dir: Path,
    rendition: Rendition,
    segment_seconds: int,
    threads: int,
    source_duration: float = 0.0,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    variant_dir = output_dir / rendition.label
    variant_dir.mkdir(parents=True, exist_ok=True)
    playlist_path = variant_dir / "index.m3u8"
    segment_pattern = variant_dir / "segment_%05d.ts"

    # `-progress pipe:1` streams machine-readable progress to stdout (read by
    # run_with_progress); `-nostats` silences the human stats on stderr.
    progress_flags = ["-nostats", "-progress", "pipe:1"] if on_progress else []
    command = [
        "ffmpeg", "-y", *progress_flags, "-i", str(source),
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
    if on_progress:
        run_with_progress(command, source_duration, on_progress)
    else:
        run_command(command)

    # HLS master playlists require a BANDWIDTH per variant. Prefer the target
    # bitrate, else the cap (maxrate); with neither (uncapped CRF) measure the
    # actual encoded segments.
    if rendition.videoBitrate:
        video_bps = bitrate_to_int(rendition.videoBitrate)
    elif rendition.maxrate:
        video_bps = bitrate_to_int(rendition.maxrate)
    else:
        segment_bytes = sum(p.stat().st_size for p in variant_dir.glob("*.ts"))
        video_bps = int(segment_bytes * 8 / source_duration) if source_duration else 0
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


def _format_timestamp(seconds: float) -> str:
    """Seconds -> WebVTT 'HH:MM:SS.mmm'."""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _thumb_quality_args(settings: Thumbnails) -> list[str]:
    """Map the normalized 1-100 quality (higher = better) onto the chosen encoder."""
    if settings.format == "webp":
        return ["-c:v", "libwebp", "-quality", str(settings.quality)]
    # mjpeg -q:v is 2 (best) .. 31 (worst) — invert the normalized scale.
    qv = round(2 + (31 - 2) * (100 - settings.quality) / 99)
    return ["-c:v", "mjpeg", "-q:v", str(qv)]


def extract_storyboard(
    source: Path,
    output_dir: Path,
    settings: Thumbnails,
    src_width: int,
    src_height: int,
    duration: float,
) -> dict[str, Any] | None:
    """Best-effort scrub thumbnails: sprite-sheet mosaics + a WebVTT index.

    Samples one frame every `interval` seconds, scales each to the requested width
    (height derived from the source aspect ratio), and tiles them into
    `columns x rows` mosaics. `storyboard.vtt` maps each timestamp to a crop region
    of a sprite. Mirrors extract_poster — never raises; on any failure it logs and
    returns None so the job still succeeds.
    """
    try:
        if duration <= 0 or src_width <= 0 or src_height <= 0:
            print("storyboard skipped: missing source duration/dimensions", flush=True)
            return None

        interval = (
            settings.intervalSeconds
            if settings.intervalSeconds is not None
            else duration / settings.targetCount
        )
        if interval <= 0:
            return None
        count = max(1, math.ceil(duration / interval))

        thumb_w = settings.width + (settings.width % 2)
        thumb_h = round(thumb_w * src_height / src_width)
        thumb_h += thumb_h % 2  # mjpeg/libwebp want even dimensions

        ext = "webp" if settings.format == "webp" else "jpg"
        cols, rows = settings.columns, settings.rows
        tiles_per_sheet = cols * rows
        rate = 1.0 / interval

        run_command(
            [
                "ffmpeg", "-y", "-i", str(source),
                "-vf", f"fps={rate:.6f},scale={thumb_w}:{thumb_h},tile={cols}x{rows}",
                "-an",
                *_thumb_quality_args(settings),
                # image2 muxer numbers from 1 by default; the VTT references sheets
                # from 0, so pin the first sprite to storyboard_000.
                "-start_number", "0",
                str(output_dir / f"storyboard_%03d.{ext}"),
            ]
        )

        sheets = sorted(output_dir.glob(f"storyboard_*.{ext}"))
        if not sheets:
            print("storyboard produced no sprites", flush=True)
            return None
        # Never reference a sprite file that wasn't written; a trailing cue may at
        # worst land on a padding tile of the last (partial) sheet.
        cues = min(count, len(sheets) * tiles_per_sheet)

        lines = ["WEBVTT", ""]
        for i in range(cues):
            sheet, pos = divmod(i, tiles_per_sheet)
            x = (pos % cols) * thumb_w
            y = (pos // cols) * thumb_h
            lines.append(
                f"{_format_timestamp(i * interval)} --> "
                f"{_format_timestamp(min((i + 1) * interval, duration))}"
            )
            lines.append(f"storyboard_{sheet:03d}.{ext}#xywh={x},{y},{thumb_w},{thumb_h}")
            lines.append("")
        (output_dir / "storyboard.vtt").write_text("\n".join(lines), encoding="utf-8")

        return {
            "interval": round(interval, 3),
            "thumbWidth": thumb_w,
            "thumbHeight": thumb_h,
            "columns": cols,
            "rows": rows,
            "format": settings.format,
            "thumbnailCount": cues,
            "spriteCount": len(sheets),
            "vttFile": "storyboard.vtt",
        }
    except Exception as error:  # storyboard is best-effort; never fail the job for it
        print(f"storyboard extraction failed: {error}", flush=True)
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
