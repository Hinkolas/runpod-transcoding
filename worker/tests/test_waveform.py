from __future__ import annotations

import numpy as np
import pytest

from waveform import PeakAccumulator, compute_peaks, scale_peaks, waveform_document


def test_peaks_per_bucket_with_short_tail():
    samples = np.array([1, -2, 3, 4, -5, 6, 7], dtype=np.int16)
    # buckets of 3: [1,-2,3] [4,-5,6] [7]
    assert compute_peaks(samples, 3).tolist() == [-2, 3, -5, 6, 7, 7]


def test_streaming_matches_one_shot_across_chunk_boundaries():
    rng = np.random.default_rng(1)
    samples = rng.integers(-32768, 32767, size=100_003, dtype=np.int16)
    expected = compute_peaks(samples, 800)
    accumulator = PeakAccumulator(800)
    position = 0
    for size in (1, 799, 800, 801, 12_345, 1_600, 7):
        accumulator.feed(samples[position : position + size])
        position += size
    while position < samples.size:
        accumulator.feed(samples[position : position + 9_999])
        position += 9_999
    np.testing.assert_array_equal(accumulator.finish(), expected)
    assert accumulator.sample_count == samples.size
    assert expected.size == 2 * -(-samples.size // 800)


def test_empty_input_gives_no_peaks():
    assert compute_peaks(np.empty(0, dtype=np.int16), 10).size == 0


def test_scale_to_8_bits_is_an_arithmetic_shift():
    peaks = np.array([-32768, -257, -256, -1, 0, 255, 256, 32767], dtype=np.int16)
    assert scale_peaks(peaks, 8).tolist() == [-128, -2, -1, -1, 0, 0, 1, 127]
    assert scale_peaks(peaks, 16).tolist() == peaks.tolist()


def test_document_shape():
    peaks = compute_peaks(np.array([0, 1000, -1000, 32767], dtype=np.int16), 2)
    document = waveform_document(peaks, 2, 8)
    assert document == {
        "version": 2,
        "channels": 1,
        "sample_rate": 16000,
        "samples_per_pixel": 2,
        "bits": 8,
        "length": 2,
        "data": [0, 3, -4, 127],
    }


def test_samples_per_pixel_must_be_positive():
    with pytest.raises(ValueError):
        PeakAccumulator(0)
