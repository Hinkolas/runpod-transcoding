"""Benchmark waveform generation on a long source.

    python tests/bench_waveform.py [--minutes 60] [--source FILE] [--keep DIR]

Generates (unless --source is given) a stereo 44.1 kHz 128k mp3 of the given
length, runs waveform.generate_waveform on it and prints wall time, CPU time and
peak memory of this process and of the ffmpeg decoder as one JSON line. Run it in
a fresh interpreter so the peak RSS reflects the waveform work only.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schema import Waveform  # noqa: E402
from waveform import generate_waveform  # noqa: E402


def _max_rss_mb(who: int) -> float:
    rss = resource.getrusage(who).ru_maxrss
    # bytes on macOS, KiB on Linux
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def make_source(path: Path, minutes: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=220:beep_factor=4:sample_rate=44100:duration={minutes * 60}",
            "-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k", str(path),
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--keep", type=Path, help="directory to keep the generated source in")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        source = args.source
        if source is None:
            folder = args.keep or work
            folder.mkdir(parents=True, exist_ok=True)
            source = folder / f"bench_{args.minutes:g}min.mp3"
            if not source.exists():
                make_source(source, args.minutes)

        rss_before = _max_rss_mb(resource.RUSAGE_SELF)
        cpu_before = time.process_time()
        started = time.perf_counter()
        result = generate_waveform(source, work, 0, Waveform(), args.minutes * 60)
        wall = time.perf_counter() - started
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        print(
            json.dumps(
                {
                    "minutes": args.minutes,
                    "ok": result is not None,
                    "length": result and result["length"],
                    "wallSeconds": round(wall, 2),
                    "pythonCpuSeconds": round(time.process_time() - cpu_before, 2),
                    "ffmpegCpuSeconds": round(children.ru_utime + children.ru_stime, 2),
                    "pythonPeakRssMb": round(_max_rss_mb(resource.RUSAGE_SELF), 1),
                    "pythonRssBeforeMb": round(rss_before, 1),
                    "ffmpegPeakRssMb": round(_max_rss_mb(resource.RUSAGE_CHILDREN), 1),
                    "waveformJsonBytes": (work / "waveform.json").stat().st_size if result else 0,
                }
            )
        )


if __name__ == "__main__":
    main()
