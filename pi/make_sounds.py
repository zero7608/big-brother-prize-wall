#!/usr/bin/env python3
"""Generate placeholder audio cues so the show has sound before you record it.

Writes spin_start.wav, tick.wav, armed.wav, bell.wav, spin_loop.wav and
reveal.wav into
assets/sounds/ using nothing but the standard library. They are deliberately plain — swap them for
your own recordings whenever you like, keeping the same filenames (or point
config.json somewhere else).

    python3 make_sounds.py            # write the files
    python3 make_sounds.py --force    # overwrite files that already exist
"""

import array
import math
import os
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SOUND_DIR = os.path.join(HERE, "assets", "sounds")
RATE = 44100


def write_wav(name, samples):
    path = os.path.join(SOUND_DIR, name)
    data = array.array("h", (max(-32767, min(32767, int(s * 32767))) for s in samples))
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(data.tobytes())
    print(f"  wrote {name}  ({len(samples) / RATE:.2f}s)")


def tick(length=0.09, freq=1500.0, decay=45.0):
    """A short percussive click, decayed to silence so it loops cleanly."""
    return [math.sin(2 * math.pi * freq * (i / RATE)) * math.exp(-decay * (i / RATE))
            for i in range(int(RATE * length))]


def beep(length=0.055, freq=1180.0):
    """The per-jump beep. Short, with a soft edge so a fast run of them does
    not turn into a buzz, and silent by the end so it never overlaps itself."""
    n = int(RATE * length)
    out = []
    for i in range(n):
        t = i / n
        env = min(1.0, t / 0.08) * math.exp(-7.0 * t)
        out.append(0.5 * env * (math.sin(2 * math.pi * freq * (i / RATE))
                                + 0.3 * math.sin(4 * math.pi * freq * (i / RATE))))
    return out


def spin_start():
    """A rising sweep: something is about to happen."""
    n = int(RATE * 0.35)
    out, phase = [], 0.0
    for i in range(n):
        t = i / n
        phase += 2 * math.pi * (320 + 900 * t * t) / RATE
        env = math.sin(math.pi * t) ** 0.6
        out.append(0.45 * env * math.sin(phase))
    return out


def spin_loop(beats=4, length=0.5):
    """A ticking bed. Each tick dies before the end, so the loop is seamless."""
    n = int(RATE * length)
    out = [0.0] * n
    click = tick()
    for b in range(beats):
        start = int(n * b / beats)
        for i, s in enumerate(click):
            if start + i < n:
                out[start + i] += 0.35 * s
    return out


def armed(length=0.42):
    """Two rising notes: the key has been read, step up to the button."""
    n = int(RATE * length)
    out = []
    for i in range(n):
        t = i / n
        freq = 523.25 if t < 0.5 else 698.46
        local = t if t < 0.5 else t - 0.5
        env = min(1.0, local / 0.02) * math.exp(-7.0 * local)
        out.append(0.42 * env * math.sin(2 * math.pi * freq * (i / RATE)))
    return out


def bell(length=1.4, freq=784.0):
    """A struck prize bell.

    A bell is not a chord: its partials sit at non-whole-number ratios of the
    fundamental, which is what makes it read as struck metal rather than an
    organ. The high partials also die away fastest, so the strike is bright and
    the tail settles onto the fundamental.
    """
    n = int(RATE * length)
    partials = [(1.00, 1.00, 2.6), (2.76, 0.62, 4.4), (5.40, 0.38, 7.0),
                (8.93, 0.22, 10.0), (13.3, 0.12, 14.0)]
    out = []
    for i in range(n):
        t = i / RATE
        strike = min(1.0, t / 0.004)
        total = sum(amp * math.exp(-decay * t)
                    * math.sin(2 * math.pi * freq * ratio * t)
                    for ratio, amp, decay in partials)
        out.append(0.42 * strike * total / len(partials))
    return out


def reveal():
    """A major chord that swells and rings: the winner is in."""
    n = int(RATE * 1.6)
    out = []
    partials = [(523.25, 1.0), (659.25, 0.8), (783.99, 0.7), (1046.5, 0.5)]
    for i in range(n):
        t = i / RATE
        attack = min(1.0, t / 0.05)
        env = attack * math.exp(-1.6 * t)
        out.append(0.30 * env * sum(a * math.sin(2 * math.pi * f * t)
                                    for f, a in partials) / len(partials))
    return out


def main():
    force = "--force" in sys.argv
    os.makedirs(SOUND_DIR, exist_ok=True)
    print(f"Writing placeholder cues into {SOUND_DIR}")
    for name, maker in (("spin_start.wav", spin_start),
                        ("tick.wav", beep),
                        ("armed.wav", armed),
                        ("bell.wav", bell),
                        ("spin_loop.wav", spin_loop),
                        ("reveal.wav", reveal)):
        if os.path.exists(os.path.join(SOUND_DIR, name)) and not force:
            print(f"  skipped {name} (already exists; --force to overwrite)")
            continue
        write_wav(name, maker())
    print("Done. Check them with:  python3 prize_wall.py --test-audio")


if __name__ == "__main__":
    main()
