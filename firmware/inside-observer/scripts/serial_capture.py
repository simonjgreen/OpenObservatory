#!/usr/bin/env python3
"""Reset the board and print what it says, for a fixed number of seconds.

The README told an operator to run this during the one flash that installs the
partition table, and it did not exist -- discovered with the device on the cable
on 2026-08-09. A verification step that cannot be run is worse than no
verification step, because it is followed by "and check the output says...".

Deliberately not `pio device monitor`: that is interactive and does not exit, so
it cannot be used in a scripted or agent-driven flash. This pulses RTS to reset
the board first, so the boot banner is actually captured rather than missed.

    python scripts/serial_capture.py /dev/ttyUSB0 40

Needs read/write on the port -- `sudo chmod 666 /dev/ttyUSB0`, or membership of
the `dialout` group -- and pyserial in whatever interpreter runs it.
"""

from __future__ import annotations

import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover - operator-facing message
    sys.exit("pyserial is not installed: pip install pyserial")

BAUD = 115200


def capture(port: str, seconds: float) -> int:
    with serial.Serial(port, BAUD, timeout=1) as link:
        # Pulse RTS to reset. Without this the banner has usually already gone
        # by the time the port opens, which is exactly the line worth reading.
        link.setDTR(False)
        link.setRTS(True)
        time.sleep(0.1)
        link.setRTS(False)

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            line = link.readline()
            if line:
                sys.stdout.write(line.decode("utf-8", "replace"))
                sys.stdout.flush()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    raise SystemExit(
        capture(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 40.0)
    )
