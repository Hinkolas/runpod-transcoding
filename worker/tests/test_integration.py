"""End-to-end: real ffmpeg, a local HTTP source/destination, handle_transcode."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import handler

pytestmark = pytest.mark.integration

VIDEO_360 = {"label": "360p", "height": 360, "crf": 28}  # default preset: High profile
# x264's ultrafast drops CABAC/8x8dct, so the stream is Constrained Baseline even
# though High is requested: CODECS must come from the encoded output.
VIDEO_240 = {"label": "240p", "height": 240, "crf": 30, "preset": "ultrafast"}
CODECS = {"240p": "avc1.42e029,mp4a.40.2", "360p": "avc1.640029,mp4a.40.2"}
THUMBNAILS = {"intervalSeconds": 2, "width": 160}


@pytest.fixture
def progress_events(monkeypatch) -> list[tuple[str, int | None]]:
    events: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        handler, "progress", lambda job, phase, percent=None: events.append((phase, percent))
    )
    return events


def run_job(local_http, media_path: Path, **fields: Any) -> dict[str, Any]:
    shutil.copy(media_path, local_http.state.source_dir / media_path.name)
    job = {
        "id": "test",
        "input": {
            "appVideoId": "item-1",
            "source": {"type": "http", "url": local_http.source_url(media_path.name)},
            "destination": {
                "type": "http",
                "baseUrl": local_http.destination_url,
                "headers": {"Authorization": "Bearer test-token"},
            },
            "segmentSeconds": 2,
            **fields,
        },
    }
    response = handler.handle_transcode(job)
    json.dumps(response)  # must be serialisable as-is
    return response


def ffprobe(path: Path, *args: str) -> dict[str, Any]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", *args, str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def received(local_http) -> Path:
    return local_http.state.output_dir


def assert_playable(playlist: Path, expect_video: bool, expect_audio: bool) -> dict[str, Any]:
    """ffprobe opens the playlist (following it into its segments)."""
    info = ffprobe(playlist, "-show_streams", "-show_format")
    types = sorted(s["codec_type"] for s in info["streams"])
    assert ("video" in types) == expect_video, types
    assert ("audio" in types) == expect_audio, types
    assert float(info["format"]["duration"]) > 0
    return info


def assert_masters_after_references(local_http) -> None:
    order = [put.path for put in local_http.state.puts]
    for master in ("master.m3u8", "audio.m3u8"):
        if master not in order:
            continue
        lines = (received(local_http) / master).read_text().splitlines()
        for ref in (line for line in lines if line and not line.startswith("#")):
            assert order.index(ref) < order.index(master)
            folder = ref.rsplit("/", 1)[0]
            for put in local_http.state.puts:
                if put.path.startswith(folder + "/") and put.path.endswith(".ts"):
                    assert order.index(put.path) < order.index(ref)


def check_waveform(local_http, response, peaks_per_second=20, bits=8, duration=10.0):
    assert response["waveform"]["file"] == "waveform.json"
    document = json.loads((received(local_http) / "waveform.json").read_text())
    assert document["version"] == 2 and document["channels"] == 1
    assert document["sample_rate"] == 16000
    assert document["samples_per_pixel"] == 16000 // peaks_per_second
    assert document["bits"] == bits
    assert document["length"] == response["waveform"]["length"]
    assert len(document["data"]) == 2 * document["length"]
    assert abs(document["length"] - duration * peaks_per_second) <= 2
    limit = 127 if bits == 8 else 32767
    assert max(document["data"]) <= limit and min(document["data"]) >= -limit - 1
    # a full-scale-ish sine: peaks well away from zero
    assert max(document["data"]) > limit // 20


AUDIO_OUTPUTS = {
    "audioRenditions": [{"label": "64k", "bitrate": "64k", "channels": 1, "sampleRate": 44100},
                        {"label": "128k"}],
    "audioDownload": {"format": "mp3"},
    "waveform": {},
}


def test_mp3_with_cover_art(local_http, media, progress_events):
    response = run_job(
        local_http, media["mp3_cover"],
        renditions=[VIDEO_360], thumbnails=THUMBNAILS, **AUDIO_OUTPUTS,
    )
    probe = response["probe"]
    assert probe["hasVideo"] is False and probe["hasAudio"] is True
    assert probe["hasCoverArt"] is True
    assert probe["videoStreamIndex"] is None and probe["audioStreamIndex"] == 0
    assert probe["width"] is None and probe["audioCodec"] == "mp3"

    assert response["renditions"] == []
    assert response["masterPlaylistUrl"] is None
    assert response["skipped"] == [
        {"output": "renditions", "label": "360p", "reason": "no_video_stream"},
        {"output": "thumbnails", "label": None, "reason": "no_video_stream"},
    ]
    assert response["storyboard"] is None
    assert response["poster"] == {"file": "poster.jpg", "source": "coverArt"}
    poster = ffprobe(received(local_http) / "poster.jpg", "-show_streams")["streams"][0]
    assert (poster["width"], poster["height"]) == (1280, 720)  # 1600x900 fitted into 1280x1280

    audio = response["audio"]
    assert audio["masterPlaylist"] == "audio.m3u8"
    assert [r["label"] for r in audio["renditions"]] == ["64k", "128k"]
    for rendition in audio["renditions"]:
        assert rendition["codecs"] == "mp4a.40.2"
        assert rendition["playlistFile"] == f"audio/{rendition['label']}/index.m3u8"
        assert rendition["bandwidth"] >= int(rendition["bitrate"][:-1]) * 1000
        info = assert_playable(received(local_http) / rendition["playlistFile"], False, True)
        stream = info["streams"][0]
        assert stream["codec_name"] == "aac" and stream["profile"] == "LC"
        assert stream["channels"] == rendition["channels"]
        assert int(stream["sample_rate"]) == rendition["sampleRate"]
    master = (received(local_http) / "audio.m3u8").read_text()
    assert master.startswith("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=")
    assert master.count('CODECS="mp4a.40.2"') == 2
    assert master.index("audio/64k/index.m3u8") < master.index("audio/128k/index.m3u8")

    download = audio["download"]
    assert download["file"] == "audio.mp3" and download["sampleRate"] == 44100
    mp3 = ffprobe(received(local_http) / "audio.mp3", "-show_streams", "-show_format")
    assert [s["codec_type"] for s in mp3["streams"]] == ["audio"]  # no cover art embedded
    assert mp3["streams"][0]["codec_name"] == "mp3"
    assert download["sizeBytes"] == (received(local_http) / "audio.mp3").stat().st_size
    assert abs(download["durationSeconds"] - 10) < 0.2

    check_waveform(local_http, response)
    assert response["loudness"] is None
    content_types = {put.path: put.content_type for put in local_http.state.puts}
    assert content_types["audio.mp3"] == "audio/mpeg"
    assert content_types["waveform.json"] == "application/json"
    assert content_types["audio/128k/segment_00000.ts"] == "video/mp2t"
    assert_masters_after_references(local_http)

    phases = [phase for phase, _ in progress_events]
    assert phases[:3] == ["downloading", "probing", "encoding"]
    assert phases[-1] == "uploading"
    assert ("encoding", 100) in progress_events


@pytest.mark.parametrize(
    "key, codec", [("wav", "pcm_s16le"), ("m4a", "aac"), ("mp4_audio_only", "aac")]
)
def test_audio_only_sources(local_http, media, progress_events, key, codec):
    response = run_job(
        local_http, media[key],
        audioRenditions=[{"label": "aac"}],
        audioDownload={"format": "m4a", "bitrate": "96k"},
        waveform={"peaksPerSecond": 50, "bits": 16},
    )
    probe = response["probe"]
    assert probe["hasVideo"] is False and probe["audioCodec"] == codec
    assert probe["hasCoverArt"] is False
    assert response["poster"] is None and response["posterUrl"] is None
    assert response["skipped"] == []
    download = response["audio"]["download"]
    assert download == {
        "file": "audio.m4a", "format": "m4a", "bitrate": "96k", "channels": 2,
        "sampleRate": 48000, "sizeBytes": download["sizeBytes"],
        "durationSeconds": download["durationSeconds"],
    }
    m4a = (received(local_http) / "audio.m4a").read_bytes()
    assert m4a.index(b"moov") < m4a.index(b"mdat")  # +faststart
    assert_playable(received(local_http) / "audio/aac/index.m3u8", False, True)
    duration = 8.0 if key == "wav" else 10.0
    check_waveform(local_http, response, peaks_per_second=50, bits=16, duration=duration)
    assert {p.path: p.content_type for p in local_http.state.puts}["audio.m4a"] == "audio/mp4"


def test_video_without_audio(local_http, media, progress_events):
    response = run_job(
        local_http, media["video_silent"],
        renditions=[VIDEO_360], **AUDIO_OUTPUTS,
    )
    assert response["probe"]["hasAudio"] is False
    assert response["audio"] is None and response["waveform"] is None
    assert response["skipped"] == [
        {"output": "audioRenditions", "label": "64k", "reason": "no_audio_stream"},
        {"output": "audioRenditions", "label": "128k", "reason": "no_audio_stream"},
        {"output": "audioDownload", "label": None, "reason": "no_audio_stream"},
        {"output": "waveform", "label": None, "reason": "no_audio_stream"},
    ]
    [rendition] = response["renditions"]
    assert rendition["codecs"] == "avc1.640029"
    assert_playable(received(local_http) / "360p/index.m3u8", True, False)
    master = (received(local_http) / "master.m3u8").read_text()
    assert 'CODECS="avc1.640029"' in master and "mp4a" not in master
    assert not (received(local_http) / "audio.m3u8").exists()
    assert response["poster"] == {"file": "poster.jpg", "source": "frame"}


def test_video_with_audio_everything(local_http, media, progress_events):
    response = run_job(
        local_http, media["video_audio"],
        renditions=[VIDEO_240, VIDEO_360, {**VIDEO_360, "label": "1080p", "height": 1080}],
        thumbnails=THUMBNAILS, **AUDIO_OUTPUTS,
    )
    probe = response["probe"]
    # stream 2 is a 1600x900 cover; the real video is the 640x360 stream 0.
    assert (probe["videoStreamIndex"], probe["audioStreamIndex"]) == (0, 1)
    assert (probe["width"], probe["height"]) == (640, 360)
    assert probe["hasCoverArt"] is True and probe["codec"] == probe["videoCodec"] == "h264"
    assert response["skipped"] == []

    # 1080p is dropped (no upscale), exactly as before.
    assert [r["label"] for r in response["renditions"]] == ["240p", "360p"]
    for rendition in response["renditions"]:
        assert rendition["codecs"] == CODECS[rendition["label"]]
        info = assert_playable(received(local_http) / rendition["playlistFile"], True, True)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert video["height"] == rendition["height"]
    master = (received(local_http) / "master.m3u8").read_text().splitlines()
    assert master[:2] == ["#EXTM3U", "#EXT-X-VERSION:3"]
    by_label = {r["label"]: r for r in response["renditions"]}
    assert (
        f'#EXT-X-STREAM-INF:BANDWIDTH={by_label["240p"]["bandwidth"]},'
        f'RESOLUTION={by_label["240p"]["width"]}x240,CODECS="avc1.42e029,mp4a.40.2"'
    ) in master
    assert master[master.index(next(l for l in master if "x240," in l)) + 1] == "240p/index.m3u8"
    bandwidths = [int(l.split("BANDWIDTH=")[1].split(",")[0]) for l in master if "BANDWIDTH" in l]
    assert bandwidths == sorted(bandwidths)
    assert response["masterPlaylistUrl"].endswith("/out/master.m3u8")

    assert response["poster"] == {"file": "poster.jpg", "source": "frame"}
    poster = ffprobe(received(local_http) / "poster.jpg", "-show_streams")["streams"][0]
    assert poster["width"] == 640
    assert response["storyboard"]["thumbnailCount"] == 4
    assert response["storyboardVttUrl"].endswith("/out/storyboard.vtt")

    assert response["audio"]["masterPlaylist"] == "audio.m3u8"
    assert len(response["audio"]["renditions"]) == 2
    assert response["audio"]["download"]["format"] == "mp3"
    check_waveform(local_http, response, duration=8.0)

    order = [put.path for put in local_http.state.puts]
    assert order[-2:] == ["storyboard_000.jpg", "storyboard.vtt"]
    assert order.index("master.m3u8") < order.index("audio.mp3") < order.index("poster.jpg")
    assert_masters_after_references(local_http)
    assert len(order) == response["uploadedObjectCount"]


def measure_loudness(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    block = result.stderr[result.stderr.rindex("{"): result.stderr.rindex("}") + 1]
    return float(json.loads(block)["input_i"])


def test_loudness_normalisation(local_http, media, progress_events):
    source_lufs = measure_loudness(media["video_audio"])
    response = run_job(
        local_http, media["video_audio"],
        renditions=[VIDEO_240],
        audioRenditions=[{"label": "128k"}],
        audioDownload={"format": "mp3"},
        waveform={"bits": 16},
        loudness={"targetLufs": -16, "truePeak": -1.5},
    )
    loudness = response["loudness"]
    assert set(loudness) == {"measuredI", "measuredTP", "measuredLRA", "targetLufs", "truePeak"}
    assert abs(loudness["measuredI"] - source_lufs) < 0.5
    assert (loudness["targetLufs"], loudness["truePeak"]) == (-16, -1.5)

    out = received(local_http)
    assert abs(measure_loudness(out / "audio.mp3") + 16) < 1.0
    assert abs(measure_loudness(out / "audio/128k/index.m3u8") + 16) < 1.0
    # The audio inside the video rendition is left alone.
    assert abs(measure_loudness(out / "240p/index.m3u8") - source_lufs) < 1.0


def test_loudness_on_a_quiet_source_reaches_the_target(local_http, media, progress_events):
    response = run_job(
        local_http, media["quiet_mp3"],
        audioDownload={"format": "m4a"}, loudness={"targetLufs": -18},
    )
    assert response["loudness"]["measuredI"] < -35
    assert abs(measure_loudness(received(local_http) / "audio.m4a") + 18) < 1.0


def test_waveform_alone(local_http, media, progress_events):
    response = run_job(local_http, media["m4a"], waveform={"peaksPerSecond": 10})
    assert response["audio"] is None
    assert [put.path for put in local_http.state.puts] == ["waveform.json"]
    check_waveform(local_http, response, peaks_per_second=10)


def test_nothing_producible_is_a_normal_response(local_http, media, progress_events):
    response = run_job(local_http, media["wav"], renditions=[VIDEO_360], thumbnails=THUMBNAILS)
    assert response["renditions"] == [] and response["audio"] is None
    assert response["waveform"] is None and response["masterPlaylistUrl"] is None
    assert response["probe"]["hasVideo"] is False and response["probe"]["hasAudio"] is True
    assert response["skipped"] == [
        {"output": "renditions", "label": "360p", "reason": "no_video_stream"},
        {"output": "thumbnails", "label": None, "reason": "no_video_stream"},
    ]
    assert local_http.state.puts == []  # a wav has no cover art, so no poster either


def test_audio_outputs_for_a_silent_video(local_http, media, progress_events):
    response = run_job(local_http, media["video_silent"], audioDownload={"format": "mp3"})
    assert response["probe"]["hasAudio"] is False
    assert response["audio"] is None and response["renditions"] == []
    assert response["skipped"] == [
        {"output": "audioDownload", "label": None, "reason": "no_audio_stream"}
    ]
    # The poster is still produced, as for every job on a video source.
    assert response["poster"] == {"file": "poster.jpg", "source": "frame"}
    assert [put.path for put in local_http.state.puts] == ["poster.jpg"]


def test_source_without_audio_or_video_fails(local_http, tmp_path, progress_events):
    subtitles = tmp_path / "subs.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    only_subtitles = tmp_path / "subs.mkv"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(subtitles), str(only_subtitles)],
        check=True,
    )
    with pytest.raises(RuntimeError, match="Source has no audio or video stream"):
        run_job(local_http, only_subtitles, waveform={})


def test_upload_retries_inside_a_job(local_http, media, progress_events, monkeypatch):
    import storage

    sleeps: list[float] = []
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)
    failures = {"/out/audio.m3u8": [503], "/out/waveform.json": [500, 429]}
    local_http.state.fail = lambda method, path: (
        failures[path].pop(0) if failures.get(path) else None
    )
    response = run_job(
        local_http, media["m4a"], audioRenditions=[{"label": "a"}], waveform={}
    )
    assert sleeps == [1, 1, 2]
    assert response["audio"]["masterPlaylist"] == "audio.m3u8"
    assert (received(local_http) / "waveform.json").exists()


def test_loudness_leaves_silence_alone(local_http, media, progress_events):
    response = run_job(
        local_http, media["silent_mp3"],
        audioRenditions=[{"label": "a64", "bitrate": "64k"}],
        audioDownload={"format": "mp3"},
        loudness={"targetLufs": -16, "truePeak": -1.5},
    )
    assert response["loudness"] is None
    assert [r["label"] for r in response["audio"]["renditions"]] == ["a64"]
    assert response["audio"]["download"]["file"] == "audio.mp3"
