"""Copy files to a MicroPython board through its raw serial REPL."""

import base64
from pathlib import Path
import sys
import time

import serial


def read_until(port, marker, timeout_s=10):
    deadline = time.monotonic() + timeout_s
    data = bytearray()
    while marker not in data and time.monotonic() < deadline:
        data.extend(port.read(1024))
    return bytes(data)


def execute(port, code, timeout_s=10):
    port.write(code.encode("utf-8") + b"\x04")
    response = read_until(port, b"\x04>", timeout_s)
    if not response.startswith(b"OK"):
        raise RuntimeError("command rejected: %r" % response)
    stdout, _, rest = response[2:].partition(b"\x04")
    stderr, _, _ = rest.partition(b"\x04")
    if stderr:
        raise RuntimeError(stderr.decode("utf-8", "replace"))
    return stdout.decode("utf-8", "replace")


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: repl_sync.py PORT FILE_OR_DIRECTORY [...]")

    roots = [Path(arg) for arg in sys.argv[2:]]
    files = []
    for root in roots:
        if root.is_dir():
            files.extend(path for path in root.rglob("*.py")
                         if "__pycache__" not in path.parts)
        else:
            files.append(root)

    directories = sorted({path.parent.as_posix() for path in files
                          if path.parent.as_posix() != "."})

    with serial.Serial(sys.argv[1], 115200, timeout=0.1) as port:
        port.write(b"\x03\x03")
        time.sleep(0.2)
        port.reset_input_buffer()
        port.write(b"\x01")
        if b"raw REPL" not in read_until(port, b">", 2):
            raise SystemExit("board did not enter raw REPL")

        for directory in directories:
            parts = directory.split("/")
            for index in range(1, len(parts) + 1):
                partial = "/".join(parts[:index])
                execute(port, "import os; exec(\"try:\\n os.mkdir(%r)\\nexcept OSError:\\n pass\")" % partial)

        execute(port, "import binascii")
        for path in files:
            remote = path.as_posix()
            data = path.read_bytes()
            execute(port, "open(%r,'wb').close()" % remote)
            for offset in range(0, len(data), 384):
                encoded = base64.b64encode(data[offset:offset + 384])
                code = (
                    "f=open(%r,'ab');"
                    "f.write(binascii.a2b_base64(%r));f.close()"
                    % (remote, encoded)
                )
                execute(port, code)
            size = execute(port, "import os; print(os.stat(%r)[6])" % remote).strip()
            if int(size) != len(data):
                raise RuntimeError("size mismatch for %s" % remote)
            print("copied %-28s %d bytes" % (remote, len(data)))

        port.write(b"\x02")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
