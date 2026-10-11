"""Which microphone carries signal, and how loud is it?

The recorder stops on ``peak >= threshold`` (default 500) and otherwise records
for the full ``max_seconds`` and reports "heard nothing". On a machine with
several front-panel jacks and paired Bluetooth headsets, the Windows *default*
input device is often a silent one -- measured here at a peak of 54 against a
threshold of 500. That is indistinguishable from a quiet room unless you can
see the levels, which is the whole job of this script.

It prints the exact ``[voice] device = N`` line to put in ``settings.toml``.

Runs under the sidecar interpreter (``env\\whisper\\python``), like the client
itself, because it needs PyAudio. It reuses the client's constants so it
measures exactly what the recorder will see, rather than a near-miss of them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Same interpreter boundary as whisper_recorder.py: this needs PyAudio, which
# lives only in the sidecar environment. src\ is added so the sample rate and
# frame size come from the client itself and cannot drift out of agreement
# with what this script claims to measure.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import whisper_recorder as recorder  # noqa: E402

CHUNK = recorder.CHUNK_SIZE
RATE = recorder.SAMPLE_RATE
FRAME_MS = 1000.0 * CHUNK / RATE


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="voice_levels",
        description="List microphones and meter one, to find a device that "
                    "actually carries your voice.",
    )
    parser.add_argument("--device", type=int, default=-1,
                        help="PyAudio input device index (-1 = Windows default)")
    parser.add_argument("--seconds", type=float, default=8.0,
                        help="how long to meter")
    parser.add_argument("--all", action="store_true",
                        help="meter every input device in turn instead of one")
    parser.add_argument("--threshold", type=int, default=500,
                        help="the [voice] threshold being tested against")
    return parser.parse_args(argv)


def meter(pa, index: int, seconds: float, threshold: int) -> tuple[int, int]:
    """Meter one device; returns (peak, frames_over_threshold).

    Reads the way the client reads -- blocking, one frame at a time -- because
    a device that returns data faster than real time is not capturing audio at
    this rate, and that is a trap worth showing rather than silently sampling.
    """
    import pyaudio

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=RATE,
        input=True,
        input_device_index=index,
        frames_per_buffer=CHUNK,
    )
    peak = 0
    over = 0
    frames = 0
    windows = max(1, int(seconds / 0.5))
    per_window = max(1, int((seconds / windows) / (FRAME_MS / 1000.0)))
    try:
        for _ in range(windows):
            window_peak = 0
            for _ in range(per_window):
                data = stream.read(CHUNK, exception_on_overflow=False)
                value = recorder.peak(data)
                window_peak = max(window_peak, value)
                frames += 1
            peak = max(peak, window_peak)
            if window_peak >= threshold:
                over += 1
            bar = "#" * int(40 * min(1.0, window_peak / 32767.0))
            print(f"\r  {window_peak:>6} |{bar:<40}|", end="", flush=True)
    finally:
        print()
        stream.stop_stream()
        stream.close()
    return peak, over


def main() -> int:
    args = parse_args()
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        try:
            default_index = pa.get_default_input_device_info().get("index")
        except OSError:
            default_index = None

        inputs = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                inputs.append((i, info))

        if not inputs:
            print("no input devices at all -- no microphone is attached or enabled")
            return 3

        print("input devices")
        for i, info in inputs:
            marker = "   <- windows default" if i == default_index else ""
            print(f"  [{i:>2}] {info['name']}{marker}")

        targets = [i for i, _ in inputs] if args.all else [args.device]
        print(
            f"\nmetering for {args.seconds:.0f}s each -- SPEAK normally now "
            f"(threshold {args.threshold})\n"
        )
        best = None
        for index in targets:
            print(f"device [{index}]")
            try:
                peak, over = meter(pa, index, args.seconds, args.threshold)
            except Exception as exc:
                print(f"  could not open: {type(exc).__name__}: {exc}")
                continue
            if over:
                verdict = f"CARRIES SIGNAL ({over} window(s) over {args.threshold})"
            elif peak >= args.threshold:
                verdict = "marginal -- reached the threshold only briefly"
            else:
                verdict = f"silent here (peak {peak} < {args.threshold})"
            print(f"  peak {peak}  ->  {verdict}")
            if over and (best is None or peak > best[1]):
                best = (index, peak)

        print()
        if args.all:
            if best is None:
                print(
                    "no device carried signal. Check that the microphone is not "
                    "muted in Windows Sound settings > Recording, and that the "
                    "one you are speaking into is the one with the moving bar."
                )
                return 1
            print(f"loudest device: [{best[0]}] at peak {best[1]}")
            print(f"\nput this in config\\settings.toml:\n  [voice]\n  device = {best[0]}")
        else:
            print(
                "run again with --all to meter every device, or set\n"
                "  [voice]\n  device = N\nin config\\settings.toml to pin one."
            )
        return 0
    finally:
        pa.terminate()


if __name__ == "__main__":
    sys.exit(main())
