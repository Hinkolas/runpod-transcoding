"""Waveform peaks in the audiowaveform / peaks.js JSON format (version 2).

ffmpeg decodes the chosen audio stream once, downmixed to mono 16-bit PCM at
`SAMPLE_RATE`, and pipes it to us. We fold it into (min, max) pairs per bucket of
`SAMPLE_RATE / peaksPerSecond` samples as it streams in, so memory stays flat no
matter how long the source is (a 1-hour file is ~115 MB of PCM, never held at once).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np

from schema import Waveform

SAMPLE_RATE = 16000
# Buckets read from the pipe per chunk (~1.6 MB of PCM at the default 800 samples/bucket).
BUCKETS_PER_CHUNK = 1024


class PeakAccumulator:
    """Streams int16 samples into per-bucket (min, max) peaks.

    Samples that don't fill a whole bucket are carried over to the next feed;
    `finish` turns any remainder into a final, shorter bucket.
    """

    def __init__(self, samples_per_pixel: int) -> None:
        if samples_per_pixel < 1:
            raise ValueError("samples_per_pixel must be >= 1")
        self.samples_per_pixel = samples_per_pixel
        self.sample_count = 0
        self._carry = np.empty(0, dtype=np.int16)
        self._mins: list[np.ndarray] = []
        self._maxs: list[np.ndarray] = []

    def feed(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        self.sample_count += samples.size
        if self._carry.size:
            samples = np.concatenate((self._carry, samples))
        whole = samples.size - samples.size % self.samples_per_pixel
        if whole:
            buckets = samples[:whole].reshape(-1, self.samples_per_pixel)
            self._mins.append(buckets.min(axis=1))
            self._maxs.append(buckets.max(axis=1))
        self._carry = samples[whole:].copy()

    def finish(self) -> np.ndarray:
        """Interleaved [min0, max0, min1, max1, ...] as int16."""
        if self._carry.size:
            self._mins.append(self._carry.min(keepdims=True))
            self._maxs.append(self._carry.max(keepdims=True))
            self._carry = np.empty(0, dtype=np.int16)
        if not self._mins:
            return np.empty(0, dtype=np.int16)
        mins = np.concatenate(self._mins)
        maxs = np.concatenate(self._maxs)
        peaks = np.empty(mins.size * 2, dtype=np.int16)
        peaks[0::2] = mins
        peaks[1::2] = maxs
        return peaks


def compute_peaks(samples: np.ndarray, samples_per_pixel: int) -> np.ndarray:
    """Peaks of an in-memory int16 array (convenience for tests and small inputs)."""
    accumulator = PeakAccumulator(samples_per_pixel)
    accumulator.feed(np.asarray(samples, dtype=np.int16))
    return accumulator.finish()


def scale_peaks(peaks: np.ndarray, bits: int) -> np.ndarray:
    """16-bit peaks as-is; 8-bit peaks are `value >> 8`, clamped to -128..127."""
    if bits == 16:
        return peaks.astype(np.int16)
    return np.clip(peaks.astype(np.int16) >> 8, -128, 127).astype(np.int8)


def waveform_document(peaks: np.ndarray, samples_per_pixel: int, bits: int) -> dict[str, Any]:
    scaled = scale_peaks(peaks, bits)
    return {
        "version": 2,
        "channels": 1,
        "sample_rate": SAMPLE_RATE,
        "samples_per_pixel": samples_per_pixel,
        "bits": bits,
        "length": scaled.size // 2,
        "data": scaled.tolist(),
    }


def generate_waveform(
    source: Path,
    output_dir: Path,
    audio_index: int,
    settings: Waveform,
    duration: float,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any] | None:
    """Best-effort `waveform.json`. Returns the response block, or None on any failure."""
    try:
        return _generate(source, output_dir, audio_index, settings, duration, on_progress)
    except Exception as error:  # waveform is best-effort; never fail the job for it
        print(f"waveform generation failed: {error}", flush=True)
        (output_dir / "waveform.json").unlink(missing_ok=True)
        return None


def _generate(
    source: Path,
    output_dir: Path,
    audio_index: int,
    settings: Waveform,
    duration: float,
    on_progress: Callable[[float], None] | None,
) -> dict[str, Any]:
    # 16000 is divisible by most rates; otherwise round down to whole samples.
    samples_per_pixel = max(1, SAMPLE_RATE // settings.peaksPerSecond)
    accumulator = PeakAccumulator(samples_per_pixel)
    expected_samples = duration * SAMPLE_RATE
    chunk_bytes = samples_per_pixel * BUCKETS_PER_CHUNK * 2
    command = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(source),
        "-map", f"0:{audio_index}", "-vn",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
    ]
    # stderr goes to a temp file so a chatty decoder can't block the stdout pipe.
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr)
        assert process.stdout is not None
        try:
            pending = b""
            while True:
                chunk = process.stdout.read(chunk_bytes)
                if not chunk:
                    break
                chunk = pending + chunk
                usable = len(chunk) - len(chunk) % 2
                pending = chunk[usable:]
                accumulator.feed(np.frombuffer(chunk[:usable], dtype="<i2"))
                if on_progress and expected_samples > 0:
                    on_progress(min(1.0, accumulator.sample_count / expected_samples))
            returncode = process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if returncode != 0:
            stderr.seek(0)
            message = stderr.read().decode("utf-8", "replace")[-2000:]
            raise RuntimeError(f"ffmpeg decode failed ({returncode}): {message}")

    peaks = accumulator.finish()
    if peaks.size == 0:
        raise RuntimeError("the audio stream decoded to no samples")
    document = waveform_document(peaks, samples_per_pixel, settings.bits)
    with open(output_dir / "waveform.json", "w", encoding="utf-8") as handle:
        json.dump(document, handle, separators=(",", ":"))
    if on_progress:
        on_progress(1.0)
    return {
        "file": "waveform.json",
        "peaksPerSecond": settings.peaksPerSecond,
        "bits": settings.bits,
        "length": document["length"],
    }
