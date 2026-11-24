from __future__ import annotations

import pytest
from pydantic import ValidationError

from schema import AudioDownload, TranscodeInput

SOURCE = {"type": "http", "url": "https://example.com/source"}
DESTINATION = {"type": "http", "baseUrl": "https://example.com/out"}
VIDEO = {"label": "720p", "height": 720, "crf": 21}


def make(**fields) -> TranscodeInput:
    return TranscodeInput.model_validate(
        {"appVideoId": "a", "source": SOURCE, "destination": DESTINATION, **fields}
    )


def test_existing_video_job_is_unchanged():
    job = make(renditions=[VIDEO])
    assert [r.label for r in job.renditions] == ["720p"]
    assert job.audioRenditions == []
    assert job.audioDownload is None
    assert job.waveform is None
    assert job.loudness is None


def test_requires_at_least_one_output():
    with pytest.raises(ValidationError, match="at least one output"):
        make()
    with pytest.raises(ValidationError, match="at least one output"):
        make(renditions=[], audioRenditions=[], loudness={})


@pytest.mark.parametrize(
    "fields",
    [
        {"audioRenditions": [{"label": "128k"}]},
        {"audioDownload": {"format": "mp3"}},
        {"waveform": {}},
    ],
)
def test_any_audio_output_alone_is_enough(fields):
    make(**fields)


def test_audio_rendition_defaults():
    job = make(audioRenditions=[{"label": "aac_128"}])
    rendition = job.audioRenditions[0]
    assert (rendition.bitrate, rendition.channels, rendition.sampleRate) == ("128k", 2, 48000)


@pytest.mark.parametrize("label", ["", "a" * 33, "has space", "dot.ted", "slash/ed", "ümlaut"])
def test_audio_rendition_label_pattern(label):
    with pytest.raises(ValidationError):
        make(audioRenditions=[{"label": label}])


def test_audio_rendition_labels_unique():
    with pytest.raises(ValidationError, match="Duplicate audioRenditions label"):
        make(audioRenditions=[{"label": "a"}, {"label": "b"}, {"label": "a"}])


@pytest.mark.parametrize("channels", [0, 9])
def test_audio_channels_range(channels):
    with pytest.raises(ValidationError):
        make(audioRenditions=[{"label": "a", "channels": channels}])


@pytest.mark.parametrize("rate", [8000, 16000, 96000])
def test_audio_sample_rate_set(rate):
    with pytest.raises(ValidationError):
        make(audioRenditions=[{"label": "a", "sampleRate": rate}])


@pytest.mark.parametrize("bitrate", ["", "fast", "0k", "-5k"])
def test_audio_bitrate_must_parse(bitrate):
    with pytest.raises(ValidationError):
        make(audioRenditions=[{"label": "a", "bitrate": bitrate}])


def test_audio_download_sample_rate_defaults_by_format():
    assert AudioDownload(format="mp3").effective_sample_rate == 44100
    assert AudioDownload(format="m4a").effective_sample_rate == 48000
    assert AudioDownload(format="mp3", sampleRate=48000).effective_sample_rate == 48000


def test_audio_download_rejects_unknown_format_and_surround_mp3():
    with pytest.raises(ValidationError):
        make(audioDownload={"format": "ogg"})
    with pytest.raises(ValidationError, match="at most 2 channels"):
        make(audioDownload={"format": "mp3", "channels": 6})
    make(audioDownload={"format": "m4a", "channels": 6})


def test_waveform_ranges():
    job = make(waveform={})
    assert (job.waveform.peaksPerSecond, job.waveform.bits) == (20, 8)
    for bad in ({"peaksPerSecond": 0}, {"peaksPerSecond": 101}, {"bits": 12}):
        with pytest.raises(ValidationError):
            make(waveform=bad)


def test_loudness_ranges():
    job = make(waveform={}, loudness={})
    assert (job.loudness.targetLufs, job.loudness.truePeak, job.loudness.lra) == (-16, -1.5, 11)
    for bad in ({"targetLufs": -71}, {"targetLufs": -4}, {"truePeak": 0.5},
                {"truePeak": -10}, {"lra": 0}, {"lra": 51}):
        with pytest.raises(ValidationError):
            make(waveform={}, loudness=bad)
