#!/usr/bin/env python3
"""Round-trip test: write a synthetic log, decode it, assert the values.

    python tools/test_decode.py

Runs on desktop CPython with no hardware attached. It exercises the REAL
core/logger.py writer and the REAL tools/decode.py reader -- not mocks -- so
a framing or scaling bug is caught here rather than after a flight.

core/logger.py is importable on desktop because it touches nothing beyond
open(), json and struct. That is deliberate: the writer that runs in flight
is the writer under test.
"""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recfmt                                              # noqa: E402
from core.logger import BinaryLogger                       # noqa: E402
from tools.decode import (                                 # noqa: E402
    LogReader, Decoder, merge_timeline, write_csv, report, LogError,
)

# ------------------------------------------------------------------ harness
PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print("  [PASS] %s%s" % (name, ("  %s" % detail) if detail else ""))
    else:
        FAILED.append((name, detail))
        print("  [FAIL] %s%s" % (name, ("  %s" % detail) if detail else ""))
    return bool(condition)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ------------------------------------------------------------- fixture data
# Accel at 0.976 mg/LSB (+/-32g), gyro at 70 mdps/LSB (+/-2000 dps).
A_MG_PER_LSB = 0.976
G_MDPS_PER_LSB = 70.0

# Bosch's own worked example from the BME280 datasheet. Using it means this
# test validates the compensation implementation against a published
# reference rather than against itself.
BOSCH_CAL = {
    "T1": 27504, "T2": 26435, "T3": -1000,
    "P1": 36477, "P2": -10685, "P3": 3024, "P4": 2855, "P5": 140,
    "P6": -7, "P7": 15500, "P8": -14600, "P9": 6000,
}
BOSCH_ADC_T = 519888
BOSCH_ADC_P = 415148
BOSCH_EXPECT_T_C = 25.08
BOSCH_EXPECT_P_PA = 100653.0

SEN65_CHANNELS = ["pm1p0", "pm2p5", "pm4p0", "pm10", "rh", "temp", "voc", "nox"]
SEN65_SCALES = {"pm1p0": 10.0, "pm2p5": 10.0, "pm4p0": 10.0, "pm10": 10.0,
                "rh": 100.0, "temp": 200.0, "voc": 10.0, "nox": 10.0}

HEADER = {
    "imu": {
        "part": "LSM6DSO32", "addr": 0x6B,
        "accel_fs_g": 32, "gyro_fs_dps": 2000, "odr_hz": 416,
        "accel_mg_per_lsb": A_MG_PER_LSB,
        "gyro_mdps_per_lsb": G_MDPS_PER_LSB,
        "temp_lsb_per_c": 256.0, "temp_offset_c": 25.0,
        "timestamp_res_us": 25.0,
    },
    "baro": {
        "part": "BME280", "has_humidity": False,
        "calibration": BOSCH_CAL, "expected_rate_hz": 25.0,
    },
    "sen65": {
        "part": "SEN65", "channels": SEN65_CHANNELS,
        "scale_factors": SEN65_SCALES, "scale_verified": False,
    },
    "test": True,
}

# Samples at 416 Hz -> one every 2404 us -> ~96 IMU ticks at 25 us.
TICKS_PER_SAMPLE = 96
N_SAMPLES = 60


def fifo_sensor_word(tag, cnt, x, y, z):
    tag_byte = ((tag & 0x1F) << 3) | ((cnt & 0x03) << 1)
    return bytes([tag_byte]) + struct.pack("<3h", x, y, z)


def fifo_timestamp_word(cnt, ticks):
    tag_byte = ((0x04 & 0x1F) << 3) | ((cnt & 0x03) << 1)
    return bytes([tag_byte]) + struct.pack("<I", ticks) + b"\x00\x00"


def build_fifo_block(start_index, n_samples):
    """Timestamp + gyro + accel per sample, exactly as the part emits them."""
    out = bytearray()
    expected = []
    for i in range(start_index, start_index + n_samples):
        cnt = i & 0x03
        ticks = i * TICKS_PER_SAMPLE
        ax, ay, az = 100 + i, 200 + i, 1024 + i
        gx, gy, gz = -50 + i, 60 + i, -70 + i

        out += fifo_timestamp_word(cnt, ticks)
        out += fifo_sensor_word(0x01, cnt, gx, gy, gz)     # gyro
        out += fifo_sensor_word(0x02, cnt, ax, ay, az)     # accel
        expected.append({
            "ticks": ticks,
            "ax": ax, "ay": ay, "az": az,
            "gx": gx, "gy": gy, "gz": gz,
        })
    return bytes(out), expected


def write_log(path):
    """Produce a synthetic log with known contents."""
    lg = BinaryLogger(path, clock=None, flush_every=1)
    lg.open(HEADER)

    lg.write_marker(0, recfmt.MARK_BOOT, "synthetic")
    lg.write_marker(1_000, recfmt.MARK_LOG_START, "")

    # Two timesync pairs an exact second apart. 40000 ticks per second at
    # 25 us/tick, so the fit must recover a slope of exactly 25.0.
    lg.write_timesync(1_000, 0)
    lg.write_timesync(1_001_000, 40_000)

    fifo_bytes, expected = build_fifo_block(0, N_SAMPLES)
    n_words = len(fifo_bytes) // recfmt.IMU_FIFO_WORD_LEN
    lg.write_imu_fifo(500_000, fifo_bytes, n_words)

    lg.write_baro(600_000, BOSCH_ADC_P, BOSCH_ADC_T, 0)

    # Last two channels use the sensor's "unknown" sentinels, which must not
    # be scaled into plausible-looking numbers.
    lg.write_sen65(700_000, (123, 456, 789, 1011, 4500, 5000, 0xFFFF, 0x7FFF), 0)

    lg.write_marker(900_000, recfmt.MARK_LOG_STOP, "done")
    stats = lg.stats()
    lg.close()
    return expected, stats


# -------------------------------------------------------------------- tests
def test_header(reader):
    print("\n--- Header")
    check("magic and version accepted", reader.format_version == recfmt.FORMAT_VERSION)
    check("header JSON round-trips", reader.header.get("test") is True)
    check("IMU config preserved",
          reader.header["imu"]["accel_mg_per_lsb"] == A_MG_PER_LSB)
    check("BME280 calibration coefficients preserved",
          {k: int(v) for k, v in reader.header["baro"]["calibration"].items()} == BOSCH_CAL,
          "per-chip values -- a log is undecodable without them")


def test_integrity(reader, decoder, stats):
    print("\n--- Integrity")
    check("no resyncs on a clean file", reader.resyncs == 0,
          "%d" % reader.resyncs)
    check("no skipped bytes", reader.skipped_bytes == 0,
          "%d" % reader.skipped_bytes)
    check("not flagged truncated", not reader.truncated)
    total = sum(decoder.counts.values())
    check("all records decoded", total == stats["records"],
          "%d decoded, %d written" % (total, stats["records"]))


def test_timebase(decoder):
    print("\n--- Timebase")
    check("both timesync pairs collected", len(decoder.timemap.pairs) == 2)
    check("linear fit succeeded", decoder.timemap.fitted)
    check("recovered 25.0 us per tick", close(decoder.timemap.slope, 25.0, 1e-9),
          "slope = %.9f" % decoder.timemap.slope)
    check("intercept matches the first pair",
          close(decoder.timemap.intercept, 1000.0, 1e-6),
          "intercept = %.6f" % decoder.timemap.intercept)


def test_imu(decoder, expected):
    print("\n--- IMU")
    rows = decoder.imu_rows
    check("all samples recovered", len(rows) == N_SAMPLES,
          "%d of %d" % (len(rows), N_SAMPLES))
    if len(rows) != N_SAMPLES:
        return

    check("accel word count", decoder.imu_words_by_tag.get(0x02) == N_SAMPLES)
    check("gyro word count", decoder.imu_words_by_tag.get(0x01) == N_SAMPLES)
    check("timestamp word count", decoder.imu_words_by_tag.get(0x04) == N_SAMPLES)

    a_scale = A_MG_PER_LSB / 1000.0
    g_scale = G_MDPS_PER_LSB / 1000.0

    worst_a = 0.0
    worst_g = 0.0
    for row, exp in zip(rows, expected):
        worst_a = max(worst_a,
                      abs(row["ax_g"] - exp["ax"] * a_scale),
                      abs(row["ay_g"] - exp["ay"] * a_scale),
                      abs(row["az_g"] - exp["az"] * a_scale))
        worst_g = max(worst_g,
                      abs(row["gx_dps"] - exp["gx"] * g_scale),
                      abs(row["gy_dps"] - exp["gy"] * g_scale),
                      abs(row["gz_dps"] - exp["gz"] * g_scale))

    check("accel scaling exact on every sample", worst_a < 1e-9,
          "worst error %.3e g" % worst_a)
    check("gyro scaling exact on every sample", worst_g < 1e-9,
          "worst error %.3e dps" % worst_g)

    mag = rows[0]["a_mag_g"]
    ax, ay, az = rows[0]["ax_g"], rows[0]["ay_g"], rows[0]["az_g"]
    check("accel magnitude computed",
          close(mag, (ax * ax + ay * ay + az * az) ** 0.5, 1e-12))

    # THE bug this test exists to catch: the next batch's timestamp word
    # arrives before the current sample set is flushed, so a naive decoder
    # stamps every sample with the following batch's time.
    worst_t = 0.0
    for row, exp in zip(rows, expected):
        want = 25.0 * exp["ticks"] + 1000.0
        worst_t = max(worst_t, abs(row["t_us"] - want))
    check("timestamps map through the fitted clock", worst_t < 1e-6,
          "worst error %.3e us" % worst_t)

    check("timestamps strictly increasing",
          all(rows[i]["t_us"] > rows[i - 1]["t_us"] for i in range(1, len(rows))))

    dt = rows[1]["t_us"] - rows[0]["t_us"]
    check("sample interval is 2400 us (416 Hz)", close(dt, 2400.0, 1e-6),
          "%.3f us" % dt)


def test_baro(decoder):
    print("\n--- Barometer")
    check("one baro record", len(decoder.baro_rows) == 1)
    if not decoder.baro_rows:
        return
    row = decoder.baro_rows[0]

    # Validated against Bosch's published worked example, not against this
    # code's own output.
    check("temperature matches the Bosch reference",
          close(row["baro_temp_c"], BOSCH_EXPECT_T_C, 0.02),
          "%.4f degC, reference %.2f" % (row["baro_temp_c"], BOSCH_EXPECT_T_C))
    check("pressure matches the Bosch reference",
          close(row["pressure_pa"], BOSCH_EXPECT_P_PA, 2.0),
          "%.2f Pa, reference %.0f" % (row["pressure_pa"], BOSCH_EXPECT_P_PA))
    check("humidity absent on a BMP-style config", "humidity_pct" not in row)
    check("altitude computed against the pad reference",
          close(row.get("altitude_agl_m"), 0.0, 1e-6),
          "single sample is its own reference, so 0.0 m")


def test_sen65(decoder):
    print("\n--- SEN65")
    check("one SEN65 record", len(decoder.sen_rows) == 1)
    if not decoder.sen_rows:
        return
    row = decoder.sen_rows[0]

    check("raw ticks preserved verbatim", row["sen65_pm1p0_raw"] == 123,
          "raw is the authoritative channel")
    check("scaling applied for display", close(row["sen65_pm1p0"], 12.3, 1e-9),
          "%.4f" % row["sen65_pm1p0"])
    check("signed channel decoded", close(row["sen65_rh"], 45.0, 1e-9),
          "4500 / 100 = %.2f %%" % row["sen65_rh"])

    # The sentinels must survive as "unknown" rather than becoming 6553.5.
    check("u16 unknown sentinel not scaled", row["sen65_voc"] is None,
          "0xFFFF stayed None")
    check("i16 unknown sentinel not scaled", row["sen65_nox"] is None,
          "0x7FFF stayed None")


def test_markers(decoder):
    print("\n--- Markers")
    check("three markers", len(decoder.markers) == 3,
          "%d" % len(decoder.markers))
    names = [m["name"] for m in decoder.markers]
    check("marker names decoded", names == ["boot", "log_start", "log_stop"],
          repr(names))
    check("marker text decoded", decoder.markers[0]["text"] == "synthetic")


def test_csv(decoder, path):
    print("\n--- CSV output")
    rows = merge_timeline(decoder)
    expected_rows = len(decoder.imu_rows) + len(decoder.baro_rows) \
        + len(decoder.sen_rows) + len(decoder.markers)
    check("merged row count", len(rows) == expected_rows,
          "%d rows" % len(rows))
    check("merged timeline sorted by time",
          all(rows[i]["t_us"] >= rows[i - 1]["t_us"] for i in range(1, len(rows))))

    n = write_csv(rows, path)
    check("CSV written", n == len(rows) and os.path.exists(path))

    with open(path) as f:
        header_line = f.readline().strip()
        body = f.readlines()
    check("CSV header has expected columns",
          "t_us" in header_line and "ax_g" in header_line
          and "pressure_pa" in header_line, header_line[:80])
    check("CSV body row count matches", len(body) == len(rows),
          "%d data rows" % len(body))


def test_truncation(source_path, tmpdir):
    """Cut the file mid-record. Everything before the cut must survive."""
    print("\n--- Truncation recovery (simulated power loss)")
    with open(source_path, "rb") as f:
        data = f.read()

    cut = len(data) - 40
    trunc_path = os.path.join(tmpdir, "truncated.bin")
    with open(trunc_path, "wb") as f:
        f.write(data[:cut])

    reader = LogReader(trunc_path).open()
    decoder = Decoder(reader).run()

    check("truncated file still opens", True)
    check("truncation detected", reader.truncated,
          "the tail was cut mid-record")
    check("IMU samples before the cut survived",
          len(decoder.imu_rows) == N_SAMPLES,
          "%d of %d" % (len(decoder.imu_rows), N_SAMPLES))
    check("decoder did not raise", True,
          "a truncated tail costs the tail, not the file")


def _record_stream_offset(data):
    """Where the records begin: fixed header plus the variable JSON blob."""
    _magic, _version, jlen = struct.unpack_from(recfmt.FILE_HDR_FMT, data, 0)
    return recfmt.FILE_HDR_LEN + jlen


def test_resync(source_path, tmpdir):
    """Inject garbage mid-stream. The parser must recover at the next record."""
    print("\n--- Resync after corruption")
    with open(source_path, "rb") as f:
        data = bytearray(f.read())

    # Corrupt the FIRST RECORD, not the header. The header is a variable
    # length JSON blob, so the record stream starts past it.
    start = _record_stream_offset(data)
    for i in range(start, min(start + 12, len(data))):
        data[i] = 0xEE

    corrupt_path = os.path.join(tmpdir, "corrupt.bin")
    with open(corrupt_path, "wb") as f:
        f.write(bytes(data))

    reader = LogReader(corrupt_path).open()
    decoder = Decoder(reader).run()

    check("corrupt file still opens", True)
    check("header survived (damage was in the record stream)", reader.header_ok)
    check("parser resynced", reader.resyncs > 0,
          "%d resync(s), %d bytes skipped" % (reader.resyncs, reader.skipped_bytes))
    check("IMU block after the damage still decoded",
          len(decoder.imu_rows) == N_SAMPLES,
          "%d of %d samples" % (len(decoder.imu_rows), N_SAMPLES))
    check("the damaged record was dropped, not misparsed",
          len(decoder.markers) < 3,
          "%d of 3 markers survived" % len(decoder.markers))


def test_corrupt_header(source_path, tmpdir):
    """A damaged header is refused by default and salvageable on request.

    Without the header the scale factors are unknown. Guessing them would
    produce numbers that look entirely plausible and are wrong -- so the
    default is to refuse, with an explicit override for a damaged card.
    """
    print("\n--- Corrupt header")
    with open(source_path, "rb") as f:
        data = bytearray(f.read())

    # Damage inside the JSON blob, leaving magic and version intact.
    for i in range(recfmt.FILE_HDR_LEN + 20, recfmt.FILE_HDR_LEN + 32):
        data[i] = 0xEE

    path = os.path.join(tmpdir, "badheader.bin")
    with open(path, "wb") as f:
        f.write(bytes(data))

    try:
        LogReader(path).open()
        check("refuses a corrupt header by default", False, "no exception")
    except LogError as e:
        check("refuses a corrupt header by default", True,
              "and suggests --salvage" if "salvage" in str(e) else "")

    reader = LogReader(path, salvage=True).open()
    decoder = Decoder(reader).run()
    check("--salvage opens the file anyway", True)
    check("salvage flags the header as untrusted", not reader.header_ok)
    check("salvage still recovers every IMU sample",
          len(decoder.imu_rows) == N_SAMPLES,
          "%d of %d -- raw values intact, scaling is default and unreliable"
          % (len(decoder.imu_rows), N_SAMPLES))
    check("salvage keeps raw SEN65 ticks",
          decoder.sen_rows and decoder.sen_rows[0]["sen65_pm1p0_raw"] == 123,
          "raw survives even with no header")


def test_bad_files(tmpdir):
    print("\n--- Rejecting bad files")

    short = os.path.join(tmpdir, "short.bin")
    with open(short, "wb") as f:
        f.write(b"RK")
    try:
        LogReader(short).open()
        check("rejects a too-short file", False, "no exception raised")
    except LogError:
        check("rejects a too-short file", True)

    badmagic = os.path.join(tmpdir, "badmagic.bin")
    with open(badmagic, "wb") as f:
        f.write(struct.pack(recfmt.FILE_HDR_FMT, b"XXXX", 1, 2) + b"{}")
    try:
        LogReader(badmagic).open()
        check("rejects wrong magic", False, "no exception raised")
    except LogError:
        check("rejects wrong magic", True)

    badver = os.path.join(tmpdir, "badver.bin")
    with open(badver, "wb") as f:
        f.write(struct.pack(recfmt.FILE_HDR_FMT, recfmt.MAGIC, 99, 2) + b"{}")
    try:
        LogReader(badver).open()
        check("refuses an unknown format version", False, "no exception raised")
    except LogError:
        check("refuses an unknown format version", True,
              "better to refuse than to misparse")


def test_report_runs(decoder, reader):
    print("\n--- Report")
    text = report(decoder, reader)
    check("report generates", len(text) > 200, "%d chars" % len(text))
    check("report names achieved rate", "achieved rate" in text)
    check("report flags unverified SEN65 scaling",
          "UNVERIFIED" in text,
          "the log said scale_verified: false")


# --------------------------------------------------------------------- main
def main():
    print("=" * 66)
    print("Binary log round-trip test")
    print("=" * 66)

    tmpdir = tempfile.mkdtemp(prefix="rktlog_")
    log_path = os.path.join(tmpdir, "flight_001.bin")
    csv_path = os.path.join(tmpdir, "flight_001.csv")

    try:
        expected, stats = write_log(log_path)
        size = os.path.getsize(log_path)
        print("\nwrote %s: %d records, %d bytes"
              % (os.path.basename(log_path), stats["records"], size))
        if size != stats["bytes"]:
            check("logger byte accounting", False,
                  "%d on disk, %d counted" % (size, stats["bytes"]))
        else:
            check("logger byte accounting", True, "%d bytes" % size)

        reader = LogReader(log_path).open()
        decoder = Decoder(reader).run()

        test_header(reader)
        test_integrity(reader, decoder, stats)
        test_timebase(decoder)
        test_imu(decoder, expected)
        test_baro(decoder)
        test_sen65(decoder)
        test_markers(decoder)
        test_csv(decoder, csv_path)
        test_report_runs(decoder, reader)
        test_truncation(log_path, tmpdir)
        test_resync(log_path, tmpdir)
        test_corrupt_header(log_path, tmpdir)
        test_bad_files(tmpdir)

    finally:
        for name in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    print("\n" + "=" * 66)
    print("%d passed, %d failed" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("\nFailures:")
        for name, detail in FAILED:
            print("  - %s  %s" % (name, detail))
    print("=" * 66)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
