"""Request contract for the transcoding worker.

All job configuration is driven by the request `input`. Storage is pluggable per
request via discriminated unions on `source` and `destination` (`type: "s3" | "http"`),
and every quality rendition — including audio settings — is fully configurable.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator


class Rendition(BaseModel):
    """A single output quality level.

    Only `label`, `height`, `videoBitrate`, `maxrate` and `bufsize` are required;
    everything else has a sensible default so a minimal request still works.
    """

    label: str
    height: int = Field(gt=0)
    width: int | None = Field(default=None, gt=0)
    # Rate control — set EXACTLY ONE of:
    #   crf          -> constant quality (best quality-per-byte; recommended for VOD)
    #   videoBitrate -> average bitrate (ABR; needed for strict bandwidth ladders)
    crf: int | None = Field(default=None, ge=0, le=51)
    videoBitrate: str | None = None
    # Peak bitrate cap, applied in both modes (recommended for streaming).
    maxrate: str
    bufsize: str
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
    renditions: list[Rendition] = Field(min_length=1)
    allowUpscale: bool = False
    segmentSeconds: int = Field(default=6, ge=2, le=20)
    threads: int = Field(default=0, ge=0)  # 0 => auto-tune to CPU count
