"""Request contract for the transcoding worker.

All job configuration is driven by the request `input`. Storage is pluggable per
request via discriminated unions on `source` and `destination` (`type: "s3" | "http"`),
and every quality rendition — including audio settings — is fully configurable.

A job lists the outputs it wants (video `renditions`, `audioRenditions`, an
`audioDownload`, a `waveform`); the worker produces what the source supports and
reports the rest as skipped.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


def _parse_bitrate(value: str) -> int:
    """Parse an ffmpeg bitrate string ('2800k', '5M', '500000') to bits/sec; 0 if unparseable."""
    text = value.strip().lower()
    try:
        if text.endswith("k"):
            return int(float(text[:-1]) * 1000)
        if text.endswith("m"):
            return int(float(text[:-1]) * 1_000_000)
        return int(float(text))
    except ValueError:
        return 0


class Rendition(BaseModel):
    """A single output quality level.

    `label` and `height` are required, plus exactly one of `crf`/`videoBitrate`.
    The `maxrate`/`bufsize` cap is optional. Everything else has a sensible default.
    """

    label: str
    height: int = Field(gt=0)
    width: int | None = Field(default=None, gt=0)
    # Rate control — set EXACTLY ONE of:
    #   crf          -> constant quality (best quality-per-byte; recommended for VOD)
    #   videoBitrate -> average bitrate (ABR; needed for strict bandwidth ladders)
    crf: int | None = Field(default=None, ge=0, le=51)
    videoBitrate: str | None = None
    # Optional peak bitrate cap (set both, or neither). Recommended for streaming.
    maxrate: str | None = None
    bufsize: str | None = None
    codec: Literal["h264", "h265"] = "h264"
    audioBitrate: str = "128k"
    # Encode tuning — all optional.
    preset: str = "veryfast"          # ffmpeg -preset (x264/x265)
    pixelFormat: str = "yuv420p"      # ffmpeg -pix_fmt
    audioCodec: str = "aac"           # ffmpeg -c:a
    audioSampleRate: int = Field(default=48000, gt=0)  # ffmpeg -ar
    audioChannels: int = Field(default=2, gt=0)        # ffmpeg -ac

    @model_validator(mode="after")
    def _check_rate_control(self) -> "Rendition":
        if (self.crf is None) == (self.videoBitrate is None):
            raise ValueError("Set exactly one of 'crf' or 'videoBitrate' per rendition")
        if (self.maxrate is None) != (self.bufsize is None):
            raise ValueError("'maxrate' and 'bufsize' must be set together (or both omitted)")
        if self.videoBitrate is not None and self.maxrate is not None:
            target, cap = _parse_bitrate(self.videoBitrate), _parse_bitrate(self.maxrate)
            if target and cap and cap < target:
                raise ValueError(
                    f"maxrate ({self.maxrate}) must be >= videoBitrate ({self.videoBitrate})"
                )
        return self


# --- Audio outputs --------------------------------------------------------------

# Sample rates accepted for every audio output (all valid for both AAC and MP3).
AudioSampleRate = Literal[22050, 24000, 32000, 44100, 48000]


def _check_audio_bitrate(value: str) -> str:
    if _parse_bitrate(value) <= 0:
        raise ValueError(f"Invalid bitrate {value!r}; use e.g. '128k' or '128000'")
    return value


class AudioRendition(BaseModel):
    """One audio-only HLS rendition (AAC-LC in MPEG-TS segments).

    Written to `audio/<label>/index.m3u8` and listed in the audio master
    playlist `audio.m3u8`.
    """

    label: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    bitrate: str = "128k"
    channels: int = Field(default=2, ge=1, le=8)
    sampleRate: AudioSampleRate = 48000

    @field_validator("bitrate")
    @classmethod
    def _check_bitrate(cls, value: str) -> str:
        return _check_audio_bitrate(value)


class AudioDownload(BaseModel):
    """One progressive audio file for download (`audio.mp3` or `audio.m4a`)."""

    format: Literal["mp3", "m4a"]
    bitrate: str = "128k"
    channels: int = Field(default=2, ge=1, le=8)
    # None => 44100 for mp3, 48000 for m4a.
    sampleRate: AudioSampleRate | None = None

    @field_validator("bitrate")
    @classmethod
    def _check_bitrate(cls, value: str) -> str:
        return _check_audio_bitrate(value)

    @model_validator(mode="after")
    def _check_channels(self) -> "AudioDownload":
        if self.format == "mp3" and self.channels > 2:
            raise ValueError("mp3 supports at most 2 channels")
        return self

    @property
    def effective_sample_rate(self) -> int:
        if self.sampleRate is not None:
            return self.sampleRate
        return 44100 if self.format == "mp3" else 48000


class Waveform(BaseModel):
    """Peak data for drawing a waveform (audiowaveform / peaks.js JSON, version 2)."""

    peaksPerSecond: int = Field(default=20, ge=1, le=100)
    bits: Literal[8, 16] = 8


class Loudness(BaseModel):
    """Two-pass EBU R128 loudness normalisation (ffmpeg loudnorm).

    Applied to the audio renditions and the audio download only: never to the
    audio muxed into video renditions, and never to the waveform.
    """

    targetLufs: float = Field(default=-16, ge=-70, le=-5)
    truePeak: float = Field(default=-1.5, ge=-9, le=0)
    lra: float = Field(default=11, ge=1, le=50)


# --- Scrub thumbnails (storyboard sprites + WebVTT) -----------------------------


class Thumbnails(BaseModel):
    """Opt-in scrub-thumbnail (storyboard) settings.

    When present on the job input, the worker samples frames across the source and
    packs them into sprite-sheet mosaics plus a WebVTT index that maps each timestamp
    to a crop region (`storyboard_000.<ext>#xywh=x,y,w,h`) — the format every
    thumbnail-aware web player consumes. Omit the whole object to disable.

    Density is `intervalSeconds` OR `targetCount` (exactly one). Per-thumb height is
    derived from the source aspect ratio, so only `width` is configurable.
    """

    # Density — set EXACTLY ONE:
    intervalSeconds: float | None = Field(default=None, gt=0)  # one thumb every N seconds
    targetCount: int | None = Field(default=None, gt=0)        # ~this many thumbs total
    width: int = Field(default=240, gt=0)        # per-thumb width; height auto from aspect
    columns: int = Field(default=5, gt=0)        # tiles per sprite row
    rows: int = Field(default=5, gt=0)           # tiles per sprite column (cols*rows per sheet)
    format: Literal["jpeg", "webp"] = "jpeg"
    # Normalized quality, higher = better. Mapped per encoder in transcode.py:
    #   webp -> libwebp -quality (0-100, higher better) -> passed through
    #   jpeg -> mjpeg   -q:v     (2 best .. 31 worst)   -> inverted
    quality: int = Field(default=75, ge=1, le=100)

    @model_validator(mode="after")
    def _check_density(self) -> "Thumbnails":
        if (self.intervalSeconds is None) == (self.targetCount is None):
            raise ValueError("Set exactly one of 'intervalSeconds' or 'targetCount'")
        return self


# --- Source (download) backends -------------------------------------------------


class S3Source(BaseModel):
    type: Literal["s3"]
    bucket: str
    key: str
    endpointUrl: str | None = None   # None => real AWS regional endpoint
    region: str = "auto"
    accessKeyId: str | None = None   # per-request; falls back to worker env
    secretAccessKey: str | None = None


class HttpSource(BaseModel):
    type: Literal["http"]
    url: str                         # may be a presigned GET URL
    headers: dict[str, str] = Field(default_factory=dict)


Source = Annotated[Union[S3Source, HttpSource], Field(discriminator="type")]


# --- Destination (upload) backends ----------------------------------------------


class S3Destination(BaseModel):
    type: Literal["s3"]
    bucket: str
    prefix: str
    endpointUrl: str | None = None
    region: str = "auto"
    accessKeyId: str | None = None
    secretAccessKey: str | None = None


class HttpDestination(BaseModel):
    type: Literal["http"]
    baseUrl: str                     # each output file is PUT to {baseUrl}/{relativePath}
    headers: dict[str, str] = Field(default_factory=dict)  # e.g. {"Authorization": "Bearer ..."}


Destination = Annotated[Union[S3Destination, HttpDestination], Field(discriminator="type")]


# --- Top-level job input --------------------------------------------------------


class TranscodeInput(BaseModel):
    appVideoId: str                  # caller-supplied correlation id, echoed in the response
    source: Source
    destination: Destination
    # Video renditions. Produced only when the source has a video stream.
    renditions: list[Rendition] = Field(default_factory=list)
    # Opt-in scrub thumbnails. None => no storyboard is generated.
    thumbnails: Thumbnails | None = None
    allowUpscale: bool = False
    segmentSeconds: int = Field(default=6, ge=2, le=20)
    threads: int = Field(default=0, ge=0)  # 0 => auto-tune to CPU count
    # Free-form passthrough, echoed verbatim in the response. Use it to tag a job
    # for usage attribution, e.g. {"app": "app-a", ...}.
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Audio outputs. Produced only when the source has an audio stream.
    audioRenditions: list[AudioRendition] = Field(default_factory=list)
    audioDownload: AudioDownload | None = None
    waveform: Waveform | None = None
    # Loudness normalisation for audioRenditions and audioDownload.
    loudness: Loudness | None = None

    @model_validator(mode="after")
    def _check_outputs(self) -> "TranscodeInput":
        labels = [rendition.label for rendition in self.audioRenditions]
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        if duplicates:
            raise ValueError(f"Duplicate audioRenditions label(s): {', '.join(duplicates)}")
        if not (self.renditions or self.audioRenditions or self.audioDownload or self.waveform):
            raise ValueError(
                "Request at least one output: renditions, audioRenditions, "
                "audioDownload or waveform"
            )
        return self
