import json
import os
import struct

import recfmt
from core import storage


path = "/sd/flight_010.bin"
storage.mount()
counts = {}
truncated = False
bad_sync = False
with open(path, "rb") as source:
    fixed = source.read(recfmt.FILE_HDR_LEN)
    magic, version, json_len = struct.unpack(recfmt.FILE_HDR_FMT, fixed)
    header = json.loads(source.read(json_len))
    while True:
        prefix = source.read(recfmt.REC_HEADER_LEN)
        if not prefix:
            break
        if len(prefix) != recfmt.REC_HEADER_LEN:
            truncated = True
            break
        sync, record_type, payload_len = struct.unpack(recfmt.REC_HEADER_FMT,
                                                        prefix)
        if sync != recfmt.SYNC:
            bad_sync = True
            break
        payload = source.read(payload_len)
        if len(payload) != payload_len:
            truncated = True
            break
        counts[record_type] = counts.get(record_type, 0) + 1

print("VALIDATE size", os.stat(path)[6])
print("VALIDATE magic", magic, "version", version)
print("VALIDATE imu_odr", header["imu"]["odr_hz"])
print("VALIDATE record_counts", counts)
print("VALIDATE truncated", truncated, "bad_sync", bad_sync)
storage.unmount()
assert magic == recfmt.MAGIC
assert version == recfmt.FORMAT_VERSION
assert counts.get(recfmt.REC_IMU_FIFO, 0) > 0
assert counts.get(recfmt.REC_BARO, 0) > 0
assert counts.get(recfmt.REC_SEN65, 0) > 0
assert not truncated and not bad_sync
print("VALIDATE PASS")
