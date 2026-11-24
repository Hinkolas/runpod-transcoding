"""1-hour waveform benchmark (slow): run with `pytest -m slow`."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HAVE_FFMPEG

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")]

BENCH = Path(__file__).with_name("bench_waveform.py")


def test_one_hour_waveform_is_fast_and_small(tmp_path):
    source = tmp_path / "bench_60min.mp3"
    # Generate in its own process so the benchmark's child rusage is ffmpeg's decode only.
    subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
         "import bench_waveform; bench_waveform.make_source(Path(sys.argv[2]), 60)",
         str(BENCH.parent), str(source)],
        check=True,
    )
    output = subprocess.run(
        [sys.executable, str(BENCH), "--source", str(source)],
        capture_output=True, text=True, check=True,
    ).stdout
    result = json.loads(output.strip().splitlines()[-1])
    print(result)
    assert result["ok"] and result["length"] == 72000
    assert result["wallSeconds"] < 30
    assert result["pythonPeakRssMb"] - result["pythonRssBeforeMb"] < 100
