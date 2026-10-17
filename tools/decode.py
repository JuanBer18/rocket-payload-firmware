#!/usr/bin/env python3
"""Decode a binary flight log to CSV.

    python tools/decode.py flight_001.bin -o flight_001.csv
    python tools/decode.py flight_001.bin --report

Runs on desktop CPython, not on the board. All the expensive work -- FIFO
word decoding, Bosch pressure compensation, SEN65 scaling, timestamp fitting
-- happens here precisely so that none of it has to happen in flight.

Scale factors and the BME280's per-chip calibration coefficients are read
from the log's own header rather than assumed. A log therefore stays
decodable even if config.py changes, and a bench sweep that alters full-scale
mid-file decodes correctly on both sides of the change.
"""

import argparse
import csv
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recfmt                                          # noqa: E402
from drivers.bme280 import (                           # noqa: E402
    compensate_t, compensate_p, compensate_h, altitude_agl_m, METRES_PER_PA,
)

TAG_GYRO = 0x01
TAG_ACCEL = 0x02
TAG_TEMP = 0x03
TAG_TIMESTAMP = 0x04
TAG_CFG_CHANGE = 0x05

TAG_NAMES = {
    TAG_GYRO: "gyro", TAG_ACCEL: "accel", TAG_TEMP: "temp",
    TAG_TIMESTAMP: "timestamp", TAG_CFG_CHANGE: "cfg_change",
}

# Channel order in the SEN65 "read measured values" response. This is fixed
# by the command's response layout, not a guess -- unlike the SCALE factors,
# which are unverified. Used only when a header is missing, so that salvaged
# raw ticks keep their identity instead of being dropped for want of a name.
DEFAULT_SEN65_CHANNELS = ["pm1p0", "pm2p5", "pm4p0", "pm10",
                          "rh", "temp", "voc", "nox"]


class LogError(Exception):
    pass


# --------------------------------------------------------------------- read
class LogReader:
    """Iterates records, recovering from damage rather than aborting.

    Power loss mid-write is an expected event, so a truncated tail must not
    cost the whole file. On a bad frame the reader scans forward for the next
    SYNC word and carries on, counting what it skipped.
    """

    def __init__(self, path, salvage=False):
        self.path = path
        # salvage: decode records even when the header is unreadable. Off by
        # default because without the header the scale factors are unknown,
        # and silently applying defaults would produce numbers that look
        # entirely plausible and are wrong. See open().
        self.salvage = salvage
        self.header = {}
        self.header_ok = False
        self.format_version = None
        self.resyncs = 0
        self.skipped_bytes = 0
        self.truncated = False
        self._data = None
        self._offset = 0

    def open(self):
        with open(self.path, "rb") as f:
            self._data = f.read()

        if len(self._data) < recfmt.FILE_HDR_LEN:
            raise LogError("file is %d bytes, too short to hold a header"
                           % len(self._data))

        magic, version, jlen = struct.unpack_from(recfmt.FILE_HDR_FMT, self._data, 0)
        if magic != recfmt.MAGIC:
            raise LogError("bad magic %r, expected %r -- not a flight log"
                           % (magic, recfmt.MAGIC))
        if version != recfmt.FORMAT_VERSION:
            raise LogError(
                "format version %d, this decoder speaks %d. Refusing rather "
                "than misparsing it." % (version, recfmt.FORMAT_VERSION))

        self.format_version = version
        blob = self._data[recfmt.FILE_HDR_LEN:recfmt.FILE_HDR_LEN + jlen]
        try:
            self.header = json.loads(blob.decode("utf-8"))
            self.header_ok = True
        except (ValueError, UnicodeDecodeError) as e:
            # The header is written and flushed once at file open, so power
            # loss truncates the tail rather than damaging this. A corrupt
            # header means the card itself is damaged, and continuing would
            # mean guessing the scale factors -- producing plausible-looking
            # numbers that are silently wrong. Refuse by default; --salvage
            # is the deliberate override for a damaged card.
            if not self.salvage:
                raise LogError(
                    "header JSON is corrupt: %s\n"
                    "Without it the scale factors and the BME280's per-chip "
                    "calibration are unknown. Re-run with --salvage to decode "
                    "records anyway, accepting that scaled columns will be "
                    "wrong and only raw values are trustworthy." % e)
            self.header = {}
            self.header_ok = False

        self._offset = recfmt.FILE_HDR_LEN + jlen
        return self

    def records(self):
        """Yield (rec_type, payload). Resyncs past damage."""
        data = self._data
        n = len(data)

        while self._offset < n:
            if self._offset + recfmt.REC_HEADER_LEN > n:
                self.truncated = True
                self.skipped_bytes += n - self._offset
                return

            sync, rtype, plen = struct.unpack_from(
                recfmt.REC_HEADER_FMT, data, self._offset)

            if sync != recfmt.SYNC:
                if not self._resync():
                    return
                continue

            start = self._offset + recfmt.REC_HEADER_LEN
            end = start + plen
            if end > n:
                # Last record was cut off mid-write. Everything before it is
                # still good, which is the entire point of the framing.
                self.truncated = True
                self.skipped_bytes += n - self._offset
                return

            yield (rtype, data[start:end])
            self._offset = end

    def _resync(self):
        """Scan forward for the next SYNC word. -> False at end of file."""
        data = self._data
        n = len(data)
        sync_bytes = struct.pack("<H", recfmt.SYNC)
        search_from = self._offset + 1

        idx = data.find(sync_bytes, search_from)
        if idx < 0:
            self.skipped_bytes += n - self._offset
            self._offset = n
            return False

        self.resyncs += 1
        self.skipped_bytes += idx - self._offset
        self._offset = idx
        return True


# ------------------------------------------------------------------ timebase
class TimeMap:
    """Maps IMU hardware ticks to board microseconds.

    The IMU's counter is nominally 25us per tick but its oscillator is not
    trimmed, so the real figure drifts a few percent. Rather than trusting
    the nominal value, this fits a line through the (imu_ticks, board_us)
    pairs logged once a second. Samples then get placed by the sensor's own
    clock, and jitter in when Python drained the FIFO stops mattering.
    """

    def __init__(self, nominal_res_us=25.0):
        self.pairs = []
        self.nominal = nominal_res_us
        self.slope = nominal_res_us
        self.intercept = 0.0
        self.fitted = False

    def add(self, imu_ticks, board_us):
        self.pairs.append((imu_ticks, board_us))

    def fit(self):
        n = len(self.pairs)
        if n < 2:
            return False

        # Least squares. n is small (one pair per second) so this is cheap.
        sx = sum(p[0] for p in self.pairs)
        sy = sum(p[1] for p in self.pairs)
        sxx = sum(p[0] * p[0] for p in self.pairs)
        sxy = sum(p[0] * p[1] for p in self.pairs)

        denom = n * sxx - sx * sx
        if denom == 0:
            return False

        self.slope = (n * sxy - sx * sy) / denom
        self.intercept = (sy - self.slope * sx) / n
        self.fitted = True
        return True

    def to_us(self, imu_ticks):
        return self.slope * imu_ticks + self.intercept

    def drift_ppm(self):
        """How far the real tick rate is from the nominal 25us."""
        if not self.fitted or self.nominal == 0:
            return None
        return (self.slope - self.nominal) / self.nominal * 1e6


# -------------------------------------------------------------- FIFO decoding
def decode_fifo_words(payload):
    """Walk one IMU FIFO record. Yields (tag, tag_cnt, a, b, c).

    For a timestamp word the first value is the raw 32-bit counter and the
    rest are None; for accel and gyro they are the three int16 axes.
    """
    t_us, n_words = struct.unpack_from(recfmt.IMU_FIFO_HDR_FMT, payload, 0)
    off = recfmt.IMU_FIFO_HDR_LEN

    for _ in range(n_words):
        if off + recfmt.IMU_FIFO_WORD_LEN > len(payload):
            return
        tag_byte = payload[off]
        tag = (tag_byte >> 3) & 0x1F
        cnt = (tag_byte >> 1) & 0x03
        body = payload[off + 1:off + 7]

        if tag == TAG_TIMESTAMP:
            ticks = struct.unpack_from("<I", body, 0)[0]
            yield (tag, cnt, ticks, None, None)
        else:
            x, y, z = struct.unpack_from("<3h", body, 0)
            yield (tag, cnt, x, y, z)
        off += recfmt.IMU_FIFO_WORD_LEN


# ------------------------------------------------------------------- decoder
class Decoder:
    def __init__(self, reader):
        self.reader = reader
        self.header = reader.header

        imu_cfg = self.header.get("imu", {})
        self.a_scale = imu_cfg.get("accel_mg_per_lsb", 0.976) / 1000.0
        self.g_scale = imu_cfg.get("gyro_mdps_per_lsb", 70.0) / 1000.0
        self.temp_lsb = imu_cfg.get("temp_lsb_per_c", 256.0)
        self.temp_off = imu_cfg.get("temp_offset_c", 25.0)
        self.ts_res = imu_cfg.get("timestamp_res_us", 25.0)

        baro_cfg = self.header.get("baro", {})
        self.baro_cal = {k: int(v) for k, v in baro_cfg.get("calibration", {}).items()}
        self.has_humidity = baro_cfg.get("has_humidity", False)

        sen_cfg = self.header.get("sen65", {})
        self.sen_scales = sen_cfg.get("scale_factors", {})
        self.sen_channels = sen_cfg.get("channels", [])
        self.sen_scale_verified = sen_cfg.get("scale_verified", False)

        self.timemap = TimeMap(self.ts_res)

        self.imu_rows = []
        self.baro_rows = []
        self.sen_rows = []
        self.markers = []
        self.config_changes = []

        self.counts = {}
        self.imu_words_by_tag = {}
        self.total_imu_words = 0
        self.pad_pressure_pa = None

    def run(self):
        # Two passes: the first collects timesync pairs so the fit is
        # available before any IMU sample gets a timestamp assigned.
        records = list(self.reader.records())

        for rtype, payload in records:
            if rtype == recfmt.REC_TIMESYNC:
                t_us, imu_ts = struct.unpack(recfmt.TIMESYNC_FMT, payload)
                self.timemap.add(imu_ts, t_us)
        self.timemap.fit()

        for rtype, payload in records:
            self.counts[rtype] = self.counts.get(rtype, 0) + 1
            handler = {
                recfmt.REC_IMU_FIFO: self._imu,
                recfmt.REC_BARO: self._baro,
                recfmt.REC_SEN65: self._sen65,
                recfmt.REC_SEN65_RAW: self._sen65_raw,
                recfmt.REC_SEN65_NUMBER: self._sen65_number,
                recfmt.REC_MARKER: self._marker,
                recfmt.REC_CONFIG: self._config,
                recfmt.REC_TIMESYNC: lambda _p: None,
            }.get(rtype)
            if handler:
                handler(payload)

        self._compute_altitude()
        return self

    # ------------------------------------------------------------- handlers
    def _imu(self, payload):
        if len(payload) < recfmt.IMU_FIFO_HDR_LEN:
            return
        drain_us, _n_words = struct.unpack_from(recfmt.IMU_FIFO_HDR_FMT, payload, 0)

        cur_ticks = None
        pending_ticks = None
        pending = {}
        last_cnt = None

        for tag, cnt, a, b, c in decode_fifo_words(payload):
            self.total_imu_words += 1
            self.imu_words_by_tag[tag] = self.imu_words_by_tag.get(tag, 0) + 1

            if tag == TAG_TIMESTAMP:
                cur_ticks = a
                continue

            # The 2-bit tag counter increments per sample set, so a change
            # means the previous set is complete.
            if last_cnt is not None and cnt != last_cnt and pending:
                self._flush_imu(pending, pending_ticks, drain_us)
                pending = {}
            last_cnt = cnt

            # Capture the timestamp in effect when this set STARTED, not when
            # it gets flushed. The next batch's timestamp word arrives before
            # the current set is complete, so reading cur_ticks at flush time
            # would stamp every sample with the following batch's time.
            if not pending:
                pending_ticks = cur_ticks

            if tag == TAG_ACCEL:
                pending["accel"] = (a, b, c)
            elif tag == TAG_GYRO:
                pending["gyro"] = (a, b, c)
            elif tag == TAG_TEMP:
                pending["temp"] = a

        if pending:
            self._flush_imu(pending, pending_ticks, drain_us)

    def _flush_imu(self, pending, ticks, drain_us):
        if ticks is not None and self.timemap.fitted:
            t_us = self.timemap.to_us(ticks)
        elif ticks is not None:
            t_us = ticks * self.ts_res
        else:
            # No timestamp word available -- fall back to when the drain
            # happened. Coarser, and worth flagging in the report.
            t_us = drain_us

        row = {"t_us": t_us, "imu_ticks": ticks}
        if "accel" in pending:
            ax, ay, az = pending["accel"]
            row["ax_g"] = ax * self.a_scale
            row["ay_g"] = ay * self.a_scale
            row["az_g"] = az * self.a_scale
            row["a_mag_g"] = (row["ax_g"] ** 2 + row["ay_g"] ** 2
                              + row["az_g"] ** 2) ** 0.5
        if "gyro" in pending:
            gx, gy, gz = pending["gyro"]
            row["gx_dps"] = gx * self.g_scale
            row["gy_dps"] = gy * self.g_scale
            row["gz_dps"] = gz * self.g_scale
        if "temp" in pending:
            row["imu_temp_c"] = pending["temp"] / self.temp_lsb + self.temp_off
        self.imu_rows.append(row)

    def _baro(self, payload):
        if len(payload) != recfmt.BARO_LEN:
            return
        t_us, adc_p, adc_t, adc_h = struct.unpack(recfmt.BARO_FMT, payload)
        if not self.baro_cal:
            # Without the per-chip coefficients the counts cannot become
            # units. Keep the raw values so the data is not lost.
            self.baro_rows.append({"t_us": t_us, "adc_p": adc_p,
                                   "adc_t": adc_t, "adc_h": adc_h})
            return

        temp_c, t_fine = compensate_t(adc_t, self.baro_cal)
        press_pa = compensate_p(adc_p, t_fine, self.baro_cal)
        row = {"t_us": t_us, "baro_temp_c": temp_c, "pressure_pa": press_pa}
        if self.has_humidity:
            h = compensate_h(adc_h, t_fine, self.baro_cal)
            if h is not None:
                row["humidity_pct"] = h
        self.baro_rows.append(row)

    def _sen65(self, payload):
        if len(payload) != recfmt.SEN65_LEN:
            return
        vals = struct.unpack(recfmt.SEN65_FMT, payload)
        t_us = vals[0]
        words = vals[1:9]
        status = vals[9]

        row = {"t_us": t_us, "sen65_status": status}
        # Raw ticks must survive even with no header. Losing them for want of
        # a channel name would defeat the point of logging raw in the first
        # place, so fall back to the command's fixed response layout.
        names = self.sen_channels[:8] or DEFAULT_SEN65_CHANNELS
        for i, name in enumerate(names):
            raw = words[i]
            row["sen65_%s_raw" % name] = raw
            scale = self.sen_scales.get(name)
            if scale:
                # 0xFFFF / 0x7FFF are the sensor's "unknown" sentinels and
                # must not be scaled into a plausible-looking number.
                if raw in (0xFFFF, 0x7FFF):
                    row["sen65_%s" % name] = None
                else:
                    signed = raw - 65536 if raw > 32767 else raw
                    row["sen65_%s" % name] = signed / scale
        self.sen_rows.append(row)

    def _sen65_raw(self, payload):
        if len(payload) != recfmt.SEN65_RAW_LEN:
            return
        values = struct.unpack(recfmt.SEN65_RAW_FMT, payload)
        words = values[1:]
        self.sen_rows.append({
            "t_us": values[0], "_source": "sen65_raw",
            "raw_rh_pct": None if words[0] == 0x7FFF else (
                words[0] - 65536 if words[0] > 32767 else words[0]) / 100.0,
            "raw_temp_c": None if words[1] == 0x7FFF else (
                words[1] - 65536 if words[1] > 32767 else words[1]) / 200.0,
            "raw_voc_ticks": None if words[2] == 0xFFFF else words[2],
            "raw_nox_ticks": None if words[3] == 0xFFFF else words[3],
        })

    def _sen65_number(self, payload):
        if len(payload) != recfmt.SEN65_NUMBER_LEN:
            return
        values = struct.unpack(recfmt.SEN65_NUMBER_FMT, payload)
        names = ("nc0p5_per_cm3", "nc1p0_per_cm3", "nc2p5_per_cm3",
                 "nc4p0_per_cm3", "nc10_per_cm3")
        row = {"t_us": values[0], "_source": "sen65_number"}
        for name, word in zip(names, values[1:]):
            row[name] = None if word == 0xFFFF else word / 10.0
        self.sen_rows.append(row)

    def _marker(self, payload):
        if len(payload) < recfmt.MARKER_HDR_LEN:
            return
        t_us, tag = struct.unpack_from(recfmt.MARKER_HDR_FMT, payload, 0)
        text = payload[recfmt.MARKER_HDR_LEN:].decode("utf-8", "replace")
        self.markers.append({"t_us": t_us, "tag": tag,
                             "name": recfmt.MARK_NAMES.get(tag, "0x%02X" % tag),
                             "text": text})

    def _config(self, payload):
        if len(payload) < recfmt.CONFIG_HDR_LEN:
            return
        t_us = struct.unpack_from(recfmt.CONFIG_HDR_FMT, payload, 0)[0]
        blob = payload[recfmt.CONFIG_HDR_LEN:]
        try:
            snap = json.loads(blob.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        self.config_changes.append({"t_us": t_us, "config": snap})

        # A live reconfiguration changes how everything after it decodes.
        imu = snap.get("imu", {})
        if "accel_mg_per_lsb" in imu:
            self.a_scale = imu["accel_mg_per_lsb"] / 1000.0
        if "gyro_mdps_per_lsb" in imu:
            self.g_scale = imu["gyro_mdps_per_lsb"] / 1000.0

    def _compute_altitude(self):
        """AGL altitude from the first pressure readings as the pad reference."""
        pressures = [r["pressure_pa"] for r in self.baro_rows
                     if r.get("pressure_pa")]
        if not pressures:
            return
        sample = pressures[:50] if len(pressures) >= 50 else pressures
        self.pad_pressure_pa = sum(sample) / len(sample)
        for row in self.baro_rows:
            p = row.get("pressure_pa")
            if p:
                row["altitude_agl_m"] = altitude_agl_m(p, self.pad_pressure_pa)


# --------------------------------------------------------------------- merge
def merge_timeline(decoder):
    """Interleave every sensor onto one timeline, sorted by timestamp.

    Rows are sparse: an IMU row carries no pressure and vice versa. Merging
    rather than resampling keeps every sample at its true time, which is the
    property the whole design is built to preserve. Interpolate downstream if
    a dense table is wanted.
    """
    rows = []
    for r in decoder.imu_rows:
        r = dict(r)
        r["source"] = "imu"
        rows.append(r)
    for r in decoder.baro_rows:
        r = dict(r)
        r["source"] = "baro"
        rows.append(r)
    for r in decoder.sen_rows:
        r = dict(r)
        r["source"] = r.pop("_source", "sen65")
        rows.append(r)
    for r in decoder.markers:
        rows.append({"t_us": r["t_us"], "source": "marker",
                     "marker": r["name"], "marker_text": r["text"]})
    rows.sort(key=lambda r: r["t_us"])
    return rows


def write_csv(rows, path):
    if not rows:
        raise LogError("nothing to write -- no decodable records")

    preferred = [
        "t_us", "t_s", "source",
        "ax_g", "ay_g", "az_g", "a_mag_g",
        "gx_dps", "gy_dps", "gz_dps",
        "imu_temp_c", "imu_ticks",
        "pressure_pa", "altitude_agl_m", "baro_temp_c", "humidity_pct",
    ]
    keys = set()
    for r in rows:
        keys.update(r.keys())
    ordered = [k for k in preferred if k in keys]
    ordered += sorted(k for k in keys if k not in preferred)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ordered, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            r = dict(r)
            r["t_s"] = r["t_us"] / 1e6
            writer.writerow(r)
    return len(rows)


# -------------------------------------------------------------------- report
def _rate_of(rows):
    """-> (rate_hz, span_s) from the first and last timestamps."""
    if len(rows) < 2:
        return (0.0, 0.0)
    span_us = rows[-1]["t_us"] - rows[0]["t_us"]
    if span_us <= 0:
        return (0.0, 0.0)
    return ((len(rows) - 1) / (span_us / 1e6), span_us / 1e6)


def _gaps(rows, expect_hz, tolerance=3.0):
    """Find intervals more than `tolerance` times the expected period."""
    if len(rows) < 2 or expect_hz <= 0:
        return []
    expected_us = 1e6 / expect_hz
    out = []
    for i in range(1, len(rows)):
        dt = rows[i]["t_us"] - rows[i - 1]["t_us"]
        if dt > expected_us * tolerance:
            out.append((rows[i - 1]["t_us"], dt))
    return out


def report(decoder, reader):
    """The characterization numbers. This is what fills in RESULTS.md."""
    L = []
    add = L.append

    add("=" * 66)
    add("Log report: %s" % reader.path)
    add("=" * 66)

    size = os.path.getsize(reader.path)
    add("file size          %d bytes (%.2f MB)" % (size, size / 1e6))
    add("format version     %d" % reader.format_version)

    # ------------------------------------------------------- file integrity
    add("")
    add("--- Integrity")
    add("resyncs            %d" % reader.resyncs)
    add("skipped bytes      %d" % reader.skipped_bytes)
    add("truncated tail     %s" % ("YES" if reader.truncated else "no"))
    if reader.truncated:
        add("  The last record was cut off mid-write -- consistent with a")
        add("  power loss. Everything before it decoded normally, which is")
        add("  what the record framing exists to guarantee.")
    if reader.resyncs:
        add("  Damage was found mid-file and the parser recovered at the next")
        add("  sync word. %d bytes were unreadable." % reader.skipped_bytes)

    # ------------------------------------------------------------- records
    add("")
    add("--- Records")
    for rtype in sorted(decoder.counts):
        add("  %-12s %8d" % (recfmt.REC_NAMES.get(rtype, "0x%02X" % rtype),
                             decoder.counts[rtype]))

    # ---------------------------------------------------------------- IMU
    add("")
    add("--- IMU")
    add("FIFO words total   %d" % decoder.total_imu_words)
    for tag in sorted(decoder.imu_words_by_tag):
        add("  tag 0x%02X %-11s %8d"
            % (tag, TAG_NAMES.get(tag, "?"), decoder.imu_words_by_tag[tag]))

    if decoder.imu_rows:
        rate, span = _rate_of(decoder.imu_rows)
        add("samples            %d" % len(decoder.imu_rows))
        add("span               %.2f s" % span)
        add("achieved rate      %.1f Hz" % rate)

        cfg_odr = decoder.header.get("imu", {}).get("odr_hz")
        if cfg_odr:
            err = (rate - cfg_odr) / cfg_odr * 100.0
            add("configured ODR     %d Hz" % cfg_odr)
            add("rate error         %+.2f %%" % err)
            if abs(err) > 1.0:
                add("  Off by more than 1%. Either words were dropped or the")
                add("  timestamp fit is wrong. Check the overrun count from")
                add("  the bench run.")

        gaps = _gaps(decoder.imu_rows, cfg_odr or rate)
        add("gaps > 3x period   %d" % len(gaps))
        for t_us, dt in gaps[:5]:
            add("    at %.3f s: %.1f ms" % (t_us / 1e6, dt / 1000.0))
        if len(gaps) > 5:
            add("    ... and %d more" % (len(gaps) - 5))

        mags = [r["a_mag_g"] for r in decoder.imu_rows if "a_mag_g" in r]
        if mags:
            add("accel |a| min/max  %.3f / %.3f g" % (min(mags), max(mags)))
            fs = decoder.header.get("imu", {}).get("accel_fs_g")
            if fs and max(mags) > fs * 0.98:
                add("  CLIPPING: |a| reached the +/-%dg full scale. Samples at"
                    % fs)
                add("  the rail are saturated, not real values.")

    # ------------------------------------------------------------ timebase
    add("")
    add("--- Timebase")
    add("timesync pairs     %d" % len(decoder.timemap.pairs))
    if decoder.timemap.fitted:
        add("fitted tick period %.4f us (nominal %.1f us)"
            % (decoder.timemap.slope, decoder.timemap.nominal))
        ppm = decoder.timemap.drift_ppm()
        if ppm is not None:
            add("oscillator error   %+.0f ppm (%.3f %%)" % (ppm, ppm / 1e4))
        add("IMU samples are placed by the sensor's own clock, so drain")
        add("jitter does not affect their timestamps.")
    else:
        add("NOT FITTED -- fewer than 2 timesync records.")
        add("  IMU samples fell back to the drain timestamp, which is coarser")
        add("  and carries the jitter of whenever Python got to the FIFO.")

    # ---------------------------------------------------------------- baro
    add("")
    add("--- Barometer")
    if decoder.baro_rows:
        rate, span = _rate_of(decoder.baro_rows)
        add("samples            %d" % len(decoder.baro_rows))
        add("achieved rate      %.2f Hz" % rate)
        expected = decoder.header.get("baro", {}).get("expected_rate_hz")
        if expected:
            add("predicted rate     %.2f Hz" % expected)

        press = [r["pressure_pa"] for r in decoder.baro_rows
                 if r.get("pressure_pa")]
        if press:
            add("pressure min/max   %.1f / %.1f Pa" % (min(press), max(press)))
            if decoder.pad_pressure_pa:
                add("pad reference      %.1f Pa" % decoder.pad_pressure_pa)
            alts = [r["altitude_agl_m"] for r in decoder.baro_rows
                    if r.get("altitude_agl_m") is not None]
            if alts:
                add("altitude AGL max   %.2f m" % max(alts))
                add("altitude AGL min   %.2f m" % min(alts))

            # Noise from the quietest stretch available.
            quiet = press[:200] if len(press) >= 200 else press
            if len(quiet) > 10:
                mean = sum(quiet) / len(quiet)
                sd = (sum((p - mean) ** 2 for p in quiet) / len(quiet)) ** 0.5
                add("noise (first %d)   %.3f Pa = %.3f m"
                    % (len(quiet), sd, sd * METRES_PER_PA))
        elif not decoder.baro_cal:
            add("NO CALIBRATION COEFFICIENTS IN HEADER.")
            add("  Raw ADC counts were preserved but cannot be converted to")
            add("  pressure. The coefficients are per-chip -- re-read them")
            add("  from the same board and re-run.")
    else:
        add("no barometer records")

    # --------------------------------------------------------------- SEN65
    add("")
    add("--- SEN65")
    if decoder.sen_rows:
        rate, span = _rate_of(decoder.sen_rows)
        add("samples            %d" % len(decoder.sen_rows))
        add("achieved rate      %.3f Hz (hardware limit is 1 Hz)" % rate)
        if not decoder.sen_scale_verified:
            add("SCALE FACTORS UNVERIFIED -- raw ticks are authoritative,")
            add("scaled columns are indicative only. Rescale in post once")
            add("datasheet table 32 is confirmed.")
        faults = [r for r in decoder.sen_rows if r.get("sen65_status")]
        add("records with status flags set: %d" % len(faults))
    else:
        add("no SEN65 records")

    # ------------------------------------------------------------- markers
    if decoder.markers:
        add("")
        add("--- Markers")
        for m in decoder.markers:
            add("  %10.3f s  %-16s %s"
                % (m["t_us"] / 1e6, m["name"], m["text"]))

    if decoder.config_changes:
        add("")
        add("--- Live reconfigurations")
        for c in decoder.config_changes:
            add("  %10.3f s  %s" % (c["t_us"] / 1e6,
                                    json.dumps(c["config"])[:120]))

    add("=" * 66)
    return "\n".join(L)


# ---------------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile", help="binary log from the SD card")
    ap.add_argument("-o", "--output", help="CSV output path")
    ap.add_argument("--report", action="store_true",
                    help="print achieved rates, gaps and error counts")
    ap.add_argument("--header", action="store_true",
                    help="dump the log header JSON and exit")
    ap.add_argument("--salvage", action="store_true",
                    help="decode records even if the header is unreadable "
                         "(scaled columns become untrustworthy)")
    args = ap.parse_args(argv)

    try:
        reader = LogReader(args.logfile, salvage=args.salvage).open()
    except LogError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1

    if not reader.header_ok:
        print("WARNING: header unreadable, salvaging. Raw columns are valid; "
              "scaled columns use defaults and are NOT trustworthy.",
              file=sys.stderr)

    if args.header:
        print(json.dumps(reader.header, indent=2, sort_keys=True))
        return 0

    decoder = Decoder(reader).run()

    if args.report or not args.output:
        print(report(decoder, reader))

    if args.output:
        rows = merge_timeline(decoder)
        n = write_csv(rows, args.output)
        print("\nwrote %d rows to %s" % (n, args.output))

    return 0


if __name__ == "__main__":
    sys.exit(main())
