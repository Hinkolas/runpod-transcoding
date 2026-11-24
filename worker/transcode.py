"""ffmpeg / ffprobe logic.

Pure local-filesystem operations — no storage, no RunPod. Each rendition (video
or audio) is encoded in its own ffmpeg process and they run in parallel (ffmpeg
releases the GIL in a subprocess), so a many-core box is saturated far better
than relying on a single libx264 encode's internal threading.

Every encode maps its streams by absolute index (see `probe`), so cover art in an
audio file is never mistaken for the video stream.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from schema import AudioDownload, AudioRendition, Loudness, Rendition, Thumbnails


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
) -> str:
    """Run an ffmpeg command, reporting completion as a 0..1 fraction.

    Returns the command's stderr (loudnorm prints its analysis there).

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
    return "".join(stderr_tail)


def _is_attached_pic(stream: dict[str, Any]) -> bool:
    return int((stream.get("disposition") or {}).get("attached_pic") or 0) == 1


def _pick_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The stream flagged `default`, else the first one."""
    for stream in streams:
        if int((stream.get("disposition") or {}).get("default") or 0) == 1:
            return stream
    return streams[0] if streams else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _positive_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _ffprobe(path: Path) -> dict[str, Any]:
    result = run_command(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    )
    return json.loads(result.stdout or "{}")


def probe(path: Path) -> dict[str, Any]:
    """Describe the source's video and audio streams.

    A video stream flagged `attached_pic` is cover art (common in mp3/m4a), not
    video. The real video stream is the first other video stream (preferring the
    one flagged default); the audio stream is the default one, else the first.
    Every encode maps these streams by absolute index so ffmpeg can never pick
    the cover art as the video.
    """
    data = _ffprobe(path)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    videos = [s for s in streams if s.get("codec_type") == "video" and not _is_attached_pic(s)]
    covers = [s for s in streams if s.get("codec_type") == "video" and _is_attached_pic(s)]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    video = _pick_stream(videos)
    audio = _pick_stream(audios)
    if video is None and audio is None:
        raise RuntimeError("Source has no audio or video stream")

    duration = (
        _positive_float((video or {}).get("duration"))
        or _positive_float((audio or {}).get("duration"))
        or _positive_float(fmt.get("duration"))
    )
    return {
        "hasVideo": video is not None,
        "hasAudio": audio is not None,
        "width": _int_or_none(video.get("width")) if video else None,
        "height": _int_or_none(video.get("height")) if video else None,
        "durationSeconds": duration,
        "videoCodec": video.get("codec_name") if video else None,
        "audioCodec": audio.get("codec_name") if audio else None,
        "audioChannels": _int_or_none(audio.get("channels")) if audio else None,
        "audioSampleRate": _int_or_none(audio.get("sample_rate")) if audio else None,
        "audioBitrate": _int_or_none(audio.get("bit_rate")) if audio else None,
        "hasCoverArt": bool(covers),
        "format": fmt.get("format_name"),
        "bitrate": _int_or_none(fmt.get("bit_rate")) or 0,
        "videoStreamIndex": _int_or_none(video.get("index")) if video else None,
        "audioStreamIndex": _int_or_none(audio.get("index")) if audio else None,
        # Kept for clients of the 0.1 response, which called the video codec `codec`.
        "codec": video.get("codec_name") if video else None,
    }


def cover_art_stream_index(path: Path) -> int | None:
    """Absolute index of the first cover-art (attached_pic) stream, if any."""
    for stream in _ffprobe(path).get("streams", []):
        if stream.get("codec_type") == "video" and _is_attached_pic(stream):
            return _int_or_none(stream.get("index"))
    return None


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
    # What ffmpeg's scale=-2 encodes: the nearest width, rounded down to even.
    scaled = round((source_width / source_height) * target_height)
    return scaled - scaled % 2


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
    *,
    video_index: int,
    audio_index: int | None,
) -> dict[str, Any]:
    """Encode one video rendition to HLS.

    Streams are mapped by absolute index: the video stream, plus the audio stream
    only when the source has one (a silent source gives video-only segments).
    """
    variant_dir = output_dir / rendition.label
    variant_dir.mkdir(parents=True, exist_ok=True)
    playlist_path = variant_dir / "index.m3u8"
    segment_pattern = variant_dir / "segment_%05d.ts"

    # `-progress pipe:1` streams machine-readable progress to stdout (read by
    # run_with_progress); `-nostats` silences the human stats on stderr.
    progress_flags = ["-nostats", "-progress", "pipe:1"] if on_progress else []
    has_audio = audio_index is not None
    stream_maps = ["-map", f"0:{video_index}"]
    if has_audio:
        stream_maps += ["-map", f"0:{audio_index}"]
    audio_args = (
        [
            "-c:a", rendition.audioCodec,
            "-b:a", rendition.audioBitrate,
            "-ac", str(rendition.audioChannels),
            "-ar", str(rendition.audioSampleRate),
        ]
        if has_audio
        else ["-an"]
    )
    command = [
        "ffmpeg", "-y", *progress_flags, "-i", str(source),
        *stream_maps,
        "-vf", f"scale=-2:{rendition.height}",
        *encoder_args(rendition),
        "-threads", str(threads),
        "-pix_fmt", rendition.pixelFormat,
        *rate_control_args(rendition),
        # Align GOP boundaries to segment length for clean ABR switching.
        "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})",
        "-sc_threshold", "0",
        *audio_args,
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
    # actual encoded segments. The audio bitrate is added only when there is audio.
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
        "bandwidth": video_bps + (bitrate_to_int(rendition.audioBitrate) if has_audio else 0),
        "codecs": segment_codecs(first_segment(variant_dir)),
        "playlistFile": f"{rendition.label}/index.m3u8",
        "segmentPrefix": rendition.label,
    }


# --- CODECS attribute (RFC 6381) ----------------------------------------------

# ffprobe H.264 profile name -> (profile_idc, constraint flags), as hex bytes.
_H264_PROFILES = {
    "High": (0x64, 0x00),
    "Main": (0x4D, 0x40),
    "Constrained Baseline": (0x42, 0xE0),
    "Baseline": (0x42, 0x00),
}
# ffprobe HEVC profile name -> "<profile_idc>.<compatibility flags>".
_HEVC_PROFILES = {"Main": "1.6", "Main 10": "2.4"}
# ffprobe AAC profile name -> object type.
_AAC_PROFILES = {"LC": "mp4a.40.2", "HE-AAC": "mp4a.40.5", "HE-AACv2": "mp4a.40.29"}

AAC_LC_CODECS = "mp4a.40.2"


def video_codec_string(stream: dict[str, Any]) -> str | None:
    """RFC 6381 codec string for an encoded H.264/HEVC stream, or None if unknown."""
    codec, profile = stream.get("codec_name"), stream.get("profile")
    level = _int_or_none(stream.get("level"))
    if level is None or level <= 0:
        return None
    if codec == "h264" and profile in _H264_PROFILES:
        profile_idc, constraints = _H264_PROFILES[profile]
        return f"avc1.{profile_idc:02x}{constraints:02x}{level:02x}"
    if codec == "hevc" and profile in _HEVC_PROFILES:
        return f"hvc1.{_HEVC_PROFILES[profile]}.L{level}.B0"
    return None


def audio_codec_string(stream: dict[str, Any]) -> str | None:
    codec = stream.get("codec_name")
    if codec == "aac":
        return _AAC_PROFILES.get(stream.get("profile") or "")
    if codec == "mp3":
        return "mp4a.40.34"
    if codec == "ac3":
        return "ac-3"
    if codec == "eac3":
        return "ec-3"
    return None


def first_segment(variant_dir: Path) -> Path | None:
    segments = sorted(variant_dir.glob("segment_*.ts"))
    return segments[0] if segments else None


def segment_codecs(segment: Path | None) -> str:
    """The CODECS value for a rendition, read from its first encoded segment.

    Empty when any stream's codec string is unknown: a partial CODECS attribute is
    worse than none, since players may reject the variant.
    """
    if segment is None:
        return ""
    try:
        result = run_command(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_entries", "stream=codec_type,codec_name,profile,level", str(segment),
            ]
        )
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except Exception as error:
        print(f"codec probe failed for {segment}: {error}", flush=True)
        return ""
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    parts: list[str] = []
    for stream, describe in ((video, video_codec_string), (audio, audio_codec_string)):
        if stream is None:
            continue
        value = describe(stream)
        if value is None:
            return ""
        parts.append(value)
    return ",".join(parts)


# --- Loudness normalisation ----------------------------------------------------


def _number(value: float) -> str:
    return f"{value:g}"


def analyse_loudness(
    source: Path,
    audio_index: int,
    settings: Loudness,
    duration: float,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any] | None:
    """Loudnorm pass 1: measure the chosen audio stream.

    Returns None for silence, which has no loudness to normalise.

    Returns the measured values needed by pass 2. Raises with a clear message if
    the analysis fails, since normalisation was explicitly requested.
    """
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-progress", "pipe:1", "-i", str(source),
        "-map", f"0:{audio_index}", "-vn",
        "-af", (
            f"loudnorm=I={_number(settings.targetLufs)}:TP={_number(settings.truePeak)}"
            f":LRA={_number(settings.lra)}:print_format=json"
        ),
        "-f", "null", "-",
    ]
    try:
        stderr = run_with_progress(command, duration, on_progress or (lambda _: None))
    except RuntimeError as error:
        raise RuntimeError(f"Loudness analysis failed: {error}") from error

    blocks = [b for b in re.findall(r"\{[^{}]*\}", stderr) if '"input_i"' in b]
    if not blocks:
        raise RuntimeError("Loudness analysis failed: ffmpeg printed no loudnorm measurement")
    try:
        raw = json.loads(blocks[-1])
        measured = {
            key: float(raw[key])
            for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        }
    except (KeyError, ValueError) as error:
        raise RuntimeError(f"Loudness analysis failed: unreadable measurement ({error})") from error
    if not all(math.isfinite(value) for value in measured.values()):
        # Silence (or near silence) has no loudness to normalise: encode the
        # audio as it is rather than failing the whole job.
        print(f"loudness: nothing to normalise (integrated loudness {raw.get('input_i')} LUFS)", flush=True)
        return None
    return {
        "measuredI": measured["input_i"],
        "measuredTP": measured["input_tp"],
        "measuredLRA": measured["input_lra"],
        "measuredThresh": measured["input_thresh"],
        "targetOffset": measured["target_offset"],
        "targetLufs": settings.targetLufs,
        "truePeak": settings.truePeak,
        "lra": settings.lra,
    }


def loudnorm_filter(analysis: dict[str, Any]) -> str:
    """Loudnorm pass 2 filter, using the pass-1 measurement."""
    return (
        f"loudnorm=I={_number(analysis['targetLufs'])}:TP={_number(analysis['truePeak'])}"
        f":LRA={_number(analysis['lra'])}"
        f":measured_I={_number(analysis['measuredI'])}"
        f":measured_TP={_number(analysis['measuredTP'])}"
        f":measured_LRA={_number(analysis['measuredLRA'])}"
        f":measured_thresh={_number(analysis['measuredThresh'])}"
        f":offset={_number(analysis['targetOffset'])}"
        ":linear=true"
    )


# --- Audio outputs -------------------------------------------------------------


def _run_encode(
    command: list[str], duration: float, on_progress: Callable[[float], None] | None
) -> None:
    if on_progress:
        run_with_progress(command, duration, on_progress)
    else:
        run_command(command)


def _audio_input_args(
    source: Path, audio_index: int, audio_filter: str | None, on_progress: bool
) -> list[str]:
    progress_flags = ["-nostats", "-progress", "pipe:1"] if on_progress else []
    return [
        "ffmpeg", "-y", *progress_flags, "-i", str(source),
        "-map", f"0:{audio_index}", "-vn",
        *(["-af", audio_filter] if audio_filter else []),
    ]


def transcode_audio_rendition(
    source: Path,
    output_dir: Path,
    rendition: AudioRendition,
    segment_seconds: int,
    threads: int,
    source_duration: float,
    *,
    audio_index: int,
    audio_filter: str | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Encode one audio-only HLS rendition (AAC-LC in MPEG-TS segments)."""
    variant_dir = output_dir / "audio" / rendition.label
    variant_dir.mkdir(parents=True, exist_ok=True)
    command = [
        *_audio_input_args(source, audio_index, audio_filter, on_progress is not None),
        "-c:a", "aac",
        "-b:a", rendition.bitrate,
        "-ac", str(rendition.channels),
        "-ar", str(rendition.sampleRate),
        "-threads", str(threads),
        "-f", "hls",
        "-hls_time", str(segment_seconds),
        "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(variant_dir / "segment_%05d.ts"),
        str(variant_dir / "index.m3u8"),
    ]
    _run_encode(command, source_duration, on_progress)

    # The TS container adds overhead on top of the AAC bitrate, so the measured
    # rate is usually the larger (and more honest) BANDWIDTH.
    nominal = bitrate_to_int(rendition.bitrate)
    segment_bytes = sum(p.stat().st_size for p in variant_dir.glob("*.ts"))
    measured = int(segment_bytes * 8 / source_duration) if source_duration > 0 else 0
    return {
        "label": rendition.label,
        "bitrate": rendition.bitrate,
        "channels": rendition.channels,
        "sampleRate": rendition.sampleRate,
        "bandwidth": max(nominal, measured),
        "codecs": AAC_LC_CODECS,
        "playlistFile": f"audio/{rendition.label}/index.m3u8",
    }


def encode_audio_download(
    source: Path,
    output_dir: Path,
    settings: AudioDownload,
    threads: int,
    source_duration: float,
    *,
    audio_index: int,
    audio_filter: str | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Encode the progressive download: CBR mp3 (libmp3lame) or AAC in m4a."""
    file_name = f"audio.{settings.format}"
    output_path = output_dir / file_name
    sample_rate = settings.effective_sample_rate
    if settings.format == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", settings.bitrate, "-id3v2_version", "3"]
    else:
        codec_args = ["-c:a", "aac", "-b:a", settings.bitrate, "-movflags", "+faststart"]
    command = [
        *_audio_input_args(source, audio_index, audio_filter, on_progress is not None),
        *codec_args,
        "-ac", str(settings.channels),
        "-ar", str(sample_rate),
        "-threads", str(threads),
        str(output_path),
    ]
    _run_encode(command, source_duration, on_progress)

    try:
        duration = _positive_float(_ffprobe(output_path).get("format", {}).get("duration"))
    except Exception:
        duration = 0.0
    return {
        "file": file_name,
        "format": settings.format,
        "bitrate": settings.bitrate,
        "channels": settings.channels,
        "sampleRate": sample_rate,
        "sizeBytes": output_path.stat().st_size,
        "durationSeconds": round(duration or source_duration, 3),
    }


# --- Poster ---------------------------------------------------------------------


def extract_poster(
    source: Path, output_dir: Path, duration: float, video_index: int
) -> Path | None:
    """Best-effort poster: one frame of the video stream, 640px wide."""
    seek = max(0.0, min(3.0, duration / 2 if duration else 1.0))
    poster_path = output_dir / "poster.jpg"
    try:
        run_command(
            [
                "ffmpeg", "-y", "-ss", f"{seek:.2f}", "-i", str(source),
                "-map", f"0:{video_index}",
                "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(poster_path),
            ]
        )
        return poster_path if poster_path.exists() else None
    except Exception as error:  # poster is best-effort; never fail the job for it
        print(f"poster extraction failed: {error}", flush=True)
        return None


def extract_cover_art(source: Path, output_dir: Path) -> Path | None:
    """Best-effort poster from embedded cover art (audio sources).

    Re-encoded to JPEG and scaled down to fit 1280x1280, never upscaled.
    """
    poster_path = output_dir / "poster.jpg"
    try:
        index = cover_art_stream_index(source)
        if index is None:
            return None
        run_command(
            [
                "ffmpeg", "-y", "-i", str(source), "-map", f"0:{index}", "-frames:v", "1",
                "-vf", (
                    "scale='min(1280,iw)':'min(1280,ih)'"
                    ":force_original_aspect_ratio=decrease"
                ),
                "-q:v", "3", str(poster_path),
            ]
        )
        return poster_path if poster_path.exists() else None
    except Exception as error:  # poster is best-effort; never fail the job for it
        print(f"cover art extraction failed: {error}", flush=True)
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
    video_index: int,
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
                "-map", f"0:{video_index}",
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
    """Video master `master.m3u8`: one EXT-X-STREAM-INF per rendition."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for variant in sorted(variants, key=lambda v: v["bandwidth"]):
        attributes = f'BANDWIDTH={variant["bandwidth"]}'
        if variant.get("width"):
            attributes += f',RESOLUTION={variant["width"]}x{variant["height"]}'
        if variant.get("codecs"):
            attributes += f',CODECS="{variant["codecs"]}"'
        lines.append(f"#EXT-X-STREAM-INF:{attributes}")
        lines.append(variant["playlistFile"])
    (output_dir / "master.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_audio_master_playlist(output_dir: Path, variants: list[dict[str, Any]]) -> None:
    """Audio-only master `audio.m3u8`: one EXT-X-STREAM-INF per audio rendition."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for variant in sorted(variants, key=lambda v: v["bandwidth"]):
        lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={variant["bandwidth"]},CODECS="{variant["codecs"]}"')
        lines.append(variant["playlistFile"])
    (output_dir / "audio.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plan_concurrency(num_renditions: int, requested_threads: int) -> tuple[int, int]:
    """Return (max_workers, threads_per_encode) tuned to the core count."""
    cpu = os.cpu_count() or 4
    workers = max(1, num_renditions)
    if requested_threads > 0:
        return workers, requested_threads
    per_job = max(2, cpu // workers)
    return workers, per_job
