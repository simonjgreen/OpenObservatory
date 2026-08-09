"""Dump a serial port for N seconds. `pio device monitor` needs a tty; this does not."""

from __future__ import annotations

import sys
import time

import serial


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    with serial.Serial(port, 115200, timeout=0.5) as link:
        deadline = time.monotonic() + seconds
        buffer = b""
        while time.monotonic() < deadline:
            buffer += link.read(4096)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                print(line.decode("utf-8", "replace").rstrip("\r"), flush=True)


if __name__ == "__main__":
    main()
