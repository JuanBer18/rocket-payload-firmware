# recfmt.py -- the binary log format, shared by the firmware writer and the
# host decoder.
#
# This module is imported by BOTH MicroPython (core/logger.py) and desktop
# CPython (tools/decode.py). It must therefore use nothing beyond `struct`
# and plain syntax available in both. No f-strings with '=', no dataclasses,
# no typing imports.
#
# Keeping one definition means the writer and reader cannot drift apart. If
# you change a payload layout, bump FORMAT_VERSION -- the decoder refuses a
# file whose version it does not know rather than misparsing it.

import struct

MAGIC = b"RKTB"
FORMAT_VERSION = 1

# Every record starts with this so a decoder can resynchronise after damage.
# Without it, one corrupted length field sends the parser into garbage for the
# rest of the file; with it, the parser scans forward and recovers at the next
# clean record boundary. That matters because power-loss mid-write is an
# expected event here, not an exotic one.
SYNC = 0xA5C3

REC_HEADER_FMT = "<HBH"          # sync u16, type u8, payload length u16
REC_HEADER_LEN = struct.calcsize(REC_HEADER_FMT)

MAX_PAYLOAD = 0xFFFF

# ------------------------------------------------------------ record types
REC_IMU_FIFO = 0x01
REC_BARO = 0x02
REC_SEN65 = 0x03
REC_MARKER = 0x04
REC_TIMESYNC = 0x05
REC_CONFIG = 0x06
REC_SEN65_RAW = 0x07
REC_SEN65_NUMBER = 0x08

REC_NAMES = {
    REC_IMU_FIFO: "imu_fifo",
    REC_BARO: "baro",
    REC_SEN65: "sen65",
    REC_MARKER: "marker",
    REC_TIMESYNC: "timesync",
    REC_CONFIG: "config",
    REC_SEN65_RAW: "sen65_raw_signals",
    REC_SEN65_NUMBER: "sen65_number_concentration",
}

# ---------------------------------------------------------------- payloads
#
# Every payload opens with a u64 monotonic microsecond timestamp from
# core/clock.py. 8 bytes per record is cheap next to being unable to place a
# sample on the timeline.

# t_us u64, word count u16, then n_words * 7 raw FIFO bytes copied verbatim
# from the sensor. Deliberately NOT decoded on-board -- that is the whole
# reason this rig can log at kHz rates from Python.
IMU_FIFO_HDR_FMT = "<QH"
IMU_FIFO_HDR_LEN = struct.calcsize(IMU_FIFO_HDR_FMT)
IMU_FIFO_WORD_LEN = 7            # 1 tag byte + 6 data bytes

# t_us u64, raw uncompensated ADC counts. Bosch compensation runs on the host,
# so a mistake in the compensation maths is a re-run of the decoder rather
# than a corrupted flight log.
BARO_FMT = "<QIIH"               # t_us, adc_p (20-bit), adc_t (20-bit), adc_h
BARO_LEN = struct.calcsize(BARO_FMT)

# t_us u64, 8 raw 16-bit channel words, device status u32.
# Raw ticks, not scaled values: the SEN65 scale factors were never verified
# and a wrong factor must not be baked into the log.
SEN65_FMT = "<Q8HI"
SEN65_LEN = struct.calcsize(SEN65_FMT)

# SEN65 raw RH/T/VOC/NOx signals: t_us and four raw uint16 words.
SEN65_RAW_FMT = "<Q4H"
SEN65_RAW_LEN = struct.calcsize(SEN65_RAW_FMT)

# SEN65 number concentration: t_us and five raw uint16 words for PM0.5,
# PM1.0, PM2.5, PM4.0 and PM10.0, each scaled by 10 particles/cm3.
SEN65_NUMBER_FMT = "<Q5H"
SEN65_NUMBER_LEN = struct.calcsize(SEN65_NUMBER_FMT)

# t_us u64, IMU hardware timestamp u32. Written once a second so the host can
# fit the sensor clock to the board clock and place FIFO samples by the
# former. Drain jitter then stops affecting data quality.
TIMESYNC_FMT = "<QI"
TIMESYNC_LEN = struct.calcsize(TIMESYNC_FMT)

# t_us u64, tag u8, then UTF-8 text for the rest of the payload.
MARKER_HDR_FMT = "<QB"
MARKER_HDR_LEN = struct.calcsize(MARKER_HDR_FMT)

MARK_BOOT = 0x01
MARK_LOG_START = 0x02
MARK_LOG_STOP = 0x03
MARK_SENSOR_FAIL = 0x04
MARK_SENSOR_RECOVER = 0x05
MARK_USER = 0x06
MARK_TEST = 0x07

MARK_NAMES = {
    MARK_BOOT: "boot",
    MARK_LOG_START: "log_start",
    MARK_LOG_STOP: "log_stop",
    MARK_SENSOR_FAIL: "sensor_fail",
    MARK_SENSOR_RECOVER: "sensor_recover",
    MARK_USER: "user",
    MARK_TEST: "test",
}

# t_us u64, then UTF-8 JSON. Written at file open and again on any live
# reconfiguration. The decoder reads scale factors from these rather than
# assuming them, so a bench rate sweep that changes full-scale mid-file stays
# correctly decodable.
CONFIG_HDR_FMT = "<Q"
CONFIG_HDR_LEN = struct.calcsize(CONFIG_HDR_FMT)

# ------------------------------------------------------------- file header
# MAGIC, format version u16, JSON length u16, then that many bytes of JSON.
FILE_HDR_FMT = "<4sHH"
FILE_HDR_LEN = struct.calcsize(FILE_HDR_FMT)


def pack_record(rec_type, payload):
    """Frame a payload. Returns bytes ready to write."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long: %d" % len(payload))
    return struct.pack(REC_HEADER_FMT, SYNC, rec_type, len(payload)) + payload


def pack_file_header(json_bytes):
    return struct.pack(FILE_HDR_FMT, MAGIC, FORMAT_VERSION, len(json_bytes)) + json_bytes


def imu_word_count(payload_len):
    """How many FIFO words an IMU record carries, or -1 if it is malformed."""
    body = payload_len - IMU_FIFO_HDR_LEN
    if body < 0 or body % IMU_FIFO_WORD_LEN:
        return -1
    return body // IMU_FIFO_WORD_LEN
