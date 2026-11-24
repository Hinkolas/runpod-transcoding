"""Shared fixtures: import path, test media made with ffmpeg, and a local HTTP server.

The HTTP server stands in for a consumer like Vistroem: it serves source files
over GET and accepts the worker's PUTs into a temp directory, recording their
order and Content-Type. It can be told to fail requests to exercise retries.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import pytest

WORKER_DIR = Path(__file__).resolve().parent.parent
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))


HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
    )


# --- Local HTTP server ------------------------------------------------------------


@dataclass
class Put:
    path: str
    content_type: str | None
    size: int


@dataclass
class HttpServerState:
    source_dir: Path
    output_dir: Path
    puts: list[Put] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)
    # Returns an HTTP status to answer with instead of handling the request, or
    # None to handle it normally. Called as fail(method, path).
    fail: Callable[[str, str], int | None] = lambda method, path: None
    # Number of upcoming GETs to cut off half-way through the body.
    truncate_gets: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class _Handler(BaseHTTPRequestHandler):
    state: HttpServerState  # set on the subclass per server

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def _maybe_fail(self, method: str) -> bool:
        status = self.state.fail(method, self.path)
        if status is None:
            return False
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self) -> None:
        with self.state.lock:
            self.state.gets.append(self.path)
        if self._maybe_fail("GET"):
            return
        name = self.path.split("?", 1)[0].removeprefix("/source/")
        file_path = self.state.source_dir / name
        if not file_path.is_file():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        with self.state.lock:
            truncate = self.state.truncate_gets > 0
            self.state.truncate_gets -= 1 if truncate else 0
        if truncate:
            self.wfile.write(data[: len(data) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(data)

    def do_PUT(self) -> None:
        if self._maybe_fail("PUT"):
            return
        if self.headers.get("Authorization") != "Bearer test-token":
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        relative = self.path.split("?", 1)[0].removeprefix("/out/")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        target = self.state.output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        with self.state.lock:
            self.state.puts.append(Put(relative, self.headers.get("Content-Type"), len(body)))
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


@dataclass
class LocalHttp:
    base_url: str
    state: HttpServerState

    def source_url(self, name: str) -> str:
        return f"{self.base_url}/source/{name}?signature=abc"

    @property
    def destination_url(self) -> str:
        return f"{self.base_url}/out"


@pytest.fixture
def local_http(tmp_path: Path):
    source_dir = tmp_path / "served"
    output_dir = tmp_path / "received"
    source_dir.mkdir()
    output_dir.mkdir()
    state = HttpServerState(source_dir=source_dir, output_dir=output_dir)
    handler = type("Handler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LocalHttp(f"http://127.0.0.1:{server.server_address[1]}", state)
    finally:
        server.shutdown()
        server.server_close()


# --- Test media -----------------------------------------------------------------


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict[str, Path]:
    """Small sources covering every input shape the worker must handle."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    root = tmp_path_factory.mktemp("media")
    cover = root / "cover.png"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=1600x900", "-frames:v", "1", str(cover))
    sine = "sine=frequency=440:sample_rate=44100:duration=10"
    stereo = ["-ac", "2"]

    files = {
        "mp3_cover": root / "cover.mp3",
        "wav": root / "tone.wav",
        "m4a": root / "tone.m4a",
        "mp4_audio_only": root / "audio_only.mp4",
        "video_silent": root / "silent.mp4",
        "video_audio": root / "video_audio.mp4",
        "quiet_mp3": root / "quiet.mp3",
    }
    ffmpeg(
        "-f", "lavfi", "-i", sine, "-i", str(cover),
        "-map", "0:a", "-map", "1:v", *stereo,
        "-c:a", "libmp3lame", "-b:a", "128k", "-c:v", "png",
        "-disposition:v", "attached_pic", "-id3v2_version", "3",
        "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)",
        str(files["mp3_cover"]),
    )
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=8",
           *stereo, "-c:a", "pcm_s16le", str(files["wav"]))
    ffmpeg("-f", "lavfi", "-i", sine, *stereo, "-c:a", "aac", "-b:a", "96k", str(files["m4a"]))
    ffmpeg("-f", "lavfi", "-i", sine, *stereo, "-c:a", "aac", "-b:a", "96k",
           str(files["mp4_audio_only"]))
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=8",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           str(files["video_silent"]))
    # Video + audio + a large cover-art stream: without explicit mapping ffmpeg
    # would pick the 1600x900 cover as "the best video stream".
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000:duration=8",
        "-i", str(cover),
        "-map", "0:v", "-map", "1:a", "-map", "2:v",
        "-c:v:0", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:v:1", "png", "-disposition:v:1", "attached_pic",
        "-c:a", "aac", "-ac", "2",
        str(files["video_audio"]),
    )
    # A quiet source (-30 dB) for loudness normalisation.
    ffmpeg("-f", "lavfi", "-i", sine, "-af", "volume=-30dB", *stereo,
           "-c:a", "libmp3lame", "-b:a", "128k", str(files["quiet_mp3"]))
    return files
