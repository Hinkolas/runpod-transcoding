from __future__ import annotations

from pathlib import Path

import pytest
import requests

import storage
from schema import HttpDestination, HttpSource
from storage import HttpDestinationBackend, HttpSourceBackend, content_type_for, iter_output_files


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)
    return sleeps


def write_tree(root: Path, names: list[str]) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())


def test_content_types():
    assert content_type_for(Path("audio.mp3")) == "audio/mpeg"
    assert content_type_for(Path("audio.m4a")) == "audio/mp4"
    assert content_type_for(Path("waveform.json")) == "application/json"
    assert content_type_for(Path("a/index.m3u8")) == "application/vnd.apple.mpegurl"
    assert content_type_for(Path("a/segment_00000.ts")) == "video/mp2t"


def test_upload_order_puts_references_after_their_targets(tmp_path):
    names = [
        "storyboard.vtt", "storyboard_000.jpg", "poster.jpg", "waveform.json", "audio.mp3",
        "master.m3u8", "audio.m3u8", "720p/index.m3u8", "720p/segment_00000.ts",
        "720p/segment_00001.ts", "audio/128k/index.m3u8", "audio/128k/segment_00000.ts",
    ]
    write_tree(tmp_path, names)
    order = [rel for _, rel in iter_output_files(tmp_path)]
    assert order == [
        "720p/segment_00000.ts", "720p/segment_00001.ts", "audio/128k/segment_00000.ts",
        "720p/index.m3u8", "audio/128k/index.m3u8",
        "audio.m3u8", "master.m3u8",
        "audio.mp3", "waveform.json", "poster.jpg",
        "storyboard_000.jpg", "storyboard.vtt",
    ]


def destination(local_http) -> HttpDestinationBackend:
    return HttpDestinationBackend(
        HttpDestination(
            type="http",
            baseUrl=local_http.destination_url,
            headers={"Authorization": "Bearer test-token"},
        )
    )


def test_put_retries_transient_statuses(tmp_path, local_http, no_backoff):
    write_tree(tmp_path / "out", ["a/segment_00000.ts", "master.m3u8"])
    failures = {"/out/a/segment_00000.ts": [503, 429, 408]}

    def fail(method, path):
        queue = failures.get(path)
        return queue.pop(0) if queue else None

    local_http.state.fail = fail
    stored = destination(local_http).upload(tmp_path / "out")
    assert [item.relativePath for item in stored] == ["a/segment_00000.ts", "master.m3u8"]
    assert no_backoff == [1, 2, 4]
    received = local_http.state.output_dir / "a/segment_00000.ts"
    assert received.read_bytes() == b"a/segment_00000.ts"


def test_put_gives_up_after_five_attempts(tmp_path, local_http, no_backoff):
    write_tree(tmp_path / "out", ["master.m3u8"])
    local_http.state.fail = lambda method, path: 500
    with pytest.raises(requests.HTTPError, match="500"):
        destination(local_http).upload(tmp_path / "out")
    assert no_backoff == [1, 2, 4, 8]


def test_put_does_not_retry_other_client_errors(tmp_path, local_http, no_backoff):
    write_tree(tmp_path / "out", ["master.m3u8"])
    local_http.state.fail = lambda method, path: 403
    with pytest.raises(requests.HTTPError, match="403"):
        destination(local_http).upload(tmp_path / "out")
    assert no_backoff == []


def test_put_retries_connection_errors(tmp_path, no_backoff):
    write_tree(tmp_path / "out", ["master.m3u8"])
    backend = HttpDestinationBackend(
        HttpDestination(type="http", baseUrl="http://127.0.0.1:9/out")  # discard port: refused
    )
    with pytest.raises(requests.ConnectionError):
        backend.upload(tmp_path / "out")
    assert no_backoff == [1, 2, 4, 8]


def test_get_retries_then_downloads(tmp_path, local_http, no_backoff):
    (local_http.state.source_dir / "clip.bin").write_bytes(b"x" * 5000)
    statuses = [502, 425]
    local_http.state.fail = lambda method, path: statuses.pop(0) if statuses else None
    target = tmp_path / "source"
    HttpSourceBackend(HttpSource(type="http", url=local_http.source_url("clip.bin"))).download(target)
    assert target.read_bytes() == b"x" * 5000
    assert len(local_http.state.gets) == 3
    assert no_backoff == [1, 2]


def test_get_restarts_a_truncated_download(tmp_path, local_http, no_backoff):
    payload = bytes(range(256)) * 4000
    (local_http.state.source_dir / "clip.bin").write_bytes(payload)
    local_http.state.truncate_gets = 2
    target = tmp_path / "source"
    HttpSourceBackend(HttpSource(type="http", url=local_http.source_url("clip.bin"))).download(target)
    assert target.read_bytes() == payload
    assert len(local_http.state.gets) == 3
    assert no_backoff == [1, 2]


def test_get_does_not_retry_404(tmp_path, local_http, no_backoff):
    with pytest.raises(requests.HTTPError, match="404"):
        HttpSourceBackend(
            HttpSource(type="http", url=local_http.source_url("missing.bin"))
        ).download(tmp_path / "source")
    assert no_backoff == []
