#!/usr/bin/env python3
"""Write a brief Rahamin Kiosk startup twinkle as raw stereo PCM."""

import math
import struct
import sys

RATE = 48_000
AMPLITUDE = 7_000
NOTES = ((659.25, 0.18), (783.99, 0.18), (1046.50, 0.34))
GAP_SECONDS = 0.04


def tone(frequency, duration):
    frames = int(RATE * duration)
    for index in range(frames):
        position = index / frames
        envelope = min(1.0, position / 0.08, (1.0 - position) / 0.22)
        sample = int(AMPLITUDE * envelope * math.sin(2 * math.pi * frequency * index / RATE))
        yield struct.pack("<hh", sample, sample)


def main():
    silence = struct.pack("<hh", 0, 0) * int(RATE * GAP_SECONDS)
    output = sys.stdout.buffer
    for index, (frequency, duration) in enumerate(NOTES):
        output.writelines(tone(frequency, duration))
        if index < len(NOTES) - 1:
            output.write(silence)


if __name__ == "__main__":
    main()
