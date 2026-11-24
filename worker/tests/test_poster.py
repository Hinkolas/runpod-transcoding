"""Poster and cover art sizes: the source's resolution, capped, never upscaled."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import transcode
from conftest import HAVE_FFMPEG, ffmpeg

pytestmark = pytest.mark.integration


def size_of(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    stream = json.loads(out)["streams"][0]
    return stream["width"], stream["height"]


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    return tmp_path


def video(path: Path, size: str) -> Path:
    ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=5:duration=1",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path))
    return path


def mp3_with_cover(path: Path, size: str) -> Path:
    cover = path.with_suffix(".png")
    ffmpeg("-f", "lavfi", "-i", f"color=c=blue:s={size}", "-frames:v", "1", str(cover))
    ffmpeg("-f", "lavfi", "-i", "sine=duration=1", "-i", str(cover), "-map", "0:a", "-map", "1:v",
           "-c:a", "libmp3lame", "-c:v", "png", "-disposition:v", "attached_pic",
           "-id3v2_version", "3", str(path))
    return path


@pytest.mark.parametrize(
    ("size", "want"),
    [
        ("2560x1440", (1920, 1080)),  # long side capped at 1920
        ("1080x1920", (1080, 1920)),  # portrait at exactly the cap: kept
        ("1440x2560", (1080, 1920)),  # portrait above it: capped on the height
        ("640x360", (640, 360)),      # small: never upscaled
    ],
)
def test_poster_frame_keeps_source_resolution(workdir: Path, size: str, want: tuple[int, int]) -> None:
    source = video(workdir / "source.mp4", size)
    poster = transcode.extract_poster(source, workdir, duration=1.0, video_index=0)
    assert poster is not None and size_of(poster) == want


@pytest.mark.parametrize(
    ("size", "want"),
    [
        ("3200x3200", (3000, 3000)),  # capped at 3000 for podcast apps
        ("1600x900", (1600, 900)),    # kept as it is, any aspect ratio
        ("4000x2000", (3000, 1500)),
    ],
)
def test_cover_art_up_to_3000(workdir: Path, size: str, want: tuple[int, int]) -> None:
    source = mp3_with_cover(workdir / "cover.mp3", size)
    poster = transcode.extract_cover_art(source, workdir)
    assert poster is not None and size_of(poster) == want


def test_fit_filter() -> None:
    assert transcode.fit_filter(1920) == (
        "scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease"
    )
