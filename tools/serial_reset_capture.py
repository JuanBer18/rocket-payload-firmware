"""Soft-reset a MicroPython board and print its startup output."""

import sys
import time

import serial


if len(sys.argv) not in (2, 3):
    raise SystemExit("usage: serial_reset_capture.py PORT [SECONDS]")

duration = float(sys.argv[2]) if len(sys.argv) == 3 else 12.0
with serial.Serial(sys.argv[1], 115200, timeout=0.1) as port:
    port.reset_input_buffer()
    port.write(b"\x03\x04")
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        data = port.read(4096)
        if data:
            sys.stdout.write(data.decode("utf-8", "replace"))
            sys.stdout.flush()
