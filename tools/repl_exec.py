"""Run one MicroPython statement through a board's raw serial REPL.

Usage: python tools/repl_exec.py COM4 "print('hello')"
Uses only pyserial and does not copy or modify files on the board.
"""

import sys
import time

import serial


def _read_until(port, marker, timeout_s):
    deadline = time.monotonic() + timeout_s
    data = bytearray()
    while marker not in data and time.monotonic() < deadline:
        data.extend(port.read(1024))
    return bytes(data)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: repl_exec.py PORT CODE")

    port_name, code = sys.argv[1:]
    if code.startswith("@"):
        with open(code[1:], "r", encoding="utf-8") as source:
            code = source.read()
    with serial.Serial(port_name, 115200, timeout=0.1) as port:
        port.write(b"\x03\x03")
        time.sleep(0.2)
        port.reset_input_buffer()
        port.write(b"\x01")
        banner = _read_until(port, b">", 2)
        if b"raw REPL" not in banner:
            raise SystemExit("board did not enter raw REPL: %r" % banner)

        port.write(code.encode("utf-8") + b"\x04")
        # Characterization runs can legitimately take several minutes.
        response = _read_until(port, b"\x04>", 900)
        port.write(b"\x02")

    if not response.startswith(b"OK"):
        raise SystemExit("board rejected command: %r" % response)

    stdout, separator, remainder = response[2:].partition(b"\x04")
    stderr, _, _prompt = remainder.partition(b"\x04")
    if stdout:
        sys.stdout.write(stdout.decode("utf-8", "replace"))
    if separator and stderr:
        sys.stderr.write(stderr.decode("utf-8", "replace"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
