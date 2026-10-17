"""Streaming binary-to-CSV export for post-mission use on MicroPython."""

import json
import math
import os
import struct

import config
import recfmt
from drivers.bme280 import altitude_agl_m, compensate_h, compensate_p, compensate_t


TAG_GYRO = 0x01
TAG_ACCEL = 0x02
TAG_TEMP = 0x03
TAG_TIMESTAMP = 0x04

FIELDS = (
    "t_us", "source",
    "ax_g", "ay_g", "az_g", "accel_mag_g",
    "gx_dps", "gy_dps", "gz_dps", "imu_temp_c", "imu_ticks",
    "pressure_pa", "baro_temp_c", "baro_humidity_pct", "altitude_agl_m",
    "vertical_speed_mps",
    "adc_p", "adc_t", "adc_h",
    "pm1p0_ug_m3", "pm2p5_ug_m3", "pm4p0_ug_m3", "pm10_ug_m3",
    "sen_rh_pct", "sen_temp_c", "voc_index", "nox_index", "sen_status",
    "raw_rh_pct", "raw_temp_c", "raw_voc_ticks", "raw_nox_ticks",
    "nc0p5_per_cm3", "nc1p0_per_cm3", "nc2p5_per_cm3",
    "nc4p0_per_cm3", "nc10_per_cm3",
    "marker_tag", "marker_name", "marker_text", "config_json",
)


class ExportError(Exception):
    pass


def _header(source):
    fixed = source.read(recfmt.FILE_HDR_LEN)
    if len(fixed) != recfmt.FILE_HDR_LEN:
        raise ExportError("short binary header")
    magic, version, json_len = struct.unpack(recfmt.FILE_HDR_FMT, fixed)
    if magic != recfmt.MAGIC or version != recfmt.FORMAT_VERSION:
        raise ExportError("unsupported binary log")
    blob = source.read(json_len)
    if len(blob) != json_len:
        raise ExportError("truncated binary header")
    return json.loads(blob.decode("utf-8"))


def _records(source):
    while True:
        prefix = source.read(recfmt.REC_HEADER_LEN)
        if not prefix:
            return
        if len(prefix) != recfmt.REC_HEADER_LEN:
            raise ExportError("truncated record prefix")
        sync, record_type, payload_len = struct.unpack(recfmt.REC_HEADER_FMT,
                                                        prefix)
        if sync != recfmt.SYNC:
            raise ExportError("record sync lost")
        payload = source.read(payload_len)
        if len(payload) != payload_len:
            raise ExportError("truncated record payload")
        yield record_type, payload


def _fit_times(binary_path):
    pairs = []
    with open(binary_path, "rb") as source:
        header = _header(source)
        for record_type, payload in _records(source):
            if record_type == recfmt.REC_TIMESYNC and len(payload) == recfmt.TIMESYNC_LEN:
                board_us, imu_ticks = struct.unpack(recfmt.TIMESYNC_FMT, payload)
                pairs.append((imu_ticks, board_us))
    nominal = header.get("imu", {}).get("timestamp_res_us", 25.0)
    if len(pairs) < 2:
        return header, nominal, 0.0, False
    n = len(pairs)
    sx = sum(pair[0] for pair in pairs)
    sy = sum(pair[1] for pair in pairs)
    sxx = sum(pair[0] * pair[0] for pair in pairs)
    sxy = sum(pair[0] * pair[1] for pair in pairs)
    denominator = n * sxx - sx * sx
    if denominator == 0:
        return header, nominal, 0.0, False
    slope = (n * sxy - sx * sy) / denominator
    intercept = (sy - slope * sx) / n
    return header, slope, intercept, True


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return "%.9g" % value
    text = str(value)
    if any(char in text for char in (",", '"', "\r", "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text


def _write_row(target, row):
    target.write(",".join(_csv_value(row.get(field)) for field in FIELDS))
    target.write("\n")


def _signed(word):
    return word - 65536 if word > 32767 else word


def _fifo_rows(payload, a_scale, g_scale, temp_lsb, temp_offset,
               time_slope, time_intercept, fitted):
    drain_us, n_words = struct.unpack_from(recfmt.IMU_FIFO_HDR_FMT, payload, 0)
    offset = recfmt.IMU_FIFO_HDR_LEN
    current_ticks = None
    pending_ticks = None
    pending = {}
    last_counter = None

    def finish():
        if not pending:
            return None
        ticks = pending_ticks
        if ticks is None:
            t_us = drain_us
        elif fitted:
            t_us = time_slope * ticks + time_intercept
        else:
            t_us = time_slope * ticks
        row = {"t_us": int(t_us), "source": "imu", "imu_ticks": ticks}
        if "accel" in pending:
            ax, ay, az = pending["accel"]
            ax *= a_scale
            ay *= a_scale
            az *= a_scale
            row.update({"ax_g": ax, "ay_g": ay, "az_g": az,
                        "accel_mag_g": math.sqrt(ax * ax + ay * ay + az * az)})
        if "gyro" in pending:
            gx, gy, gz = pending["gyro"]
            row.update({"gx_dps": gx * g_scale, "gy_dps": gy * g_scale,
                        "gz_dps": gz * g_scale})
        if "temp" in pending:
            row["imu_temp_c"] = pending["temp"] / temp_lsb + temp_offset
        return row

    for _ in range(n_words):
        if offset + recfmt.IMU_FIFO_WORD_LEN > len(payload):
            raise ExportError("short IMU FIFO record")
        tag_byte = payload[offset]
        tag = (tag_byte >> 3) & 0x1F
        counter = (tag_byte >> 1) & 0x03
        body = payload[offset + 1:offset + 7]
        offset += recfmt.IMU_FIFO_WORD_LEN
        if tag == TAG_TIMESTAMP:
            current_ticks = struct.unpack_from("<I", body, 0)[0]
            continue
        if last_counter is not None and counter != last_counter and pending:
            row = finish()
            if row:
                yield row
            pending = {}
        last_counter = counter
        if not pending:
            pending_ticks = current_ticks
        x, y, z = struct.unpack("<3h", body)
        if tag == TAG_ACCEL:
            pending["accel"] = (x, y, z)
        elif tag == TAG_GYRO:
            pending["gyro"] = (x, y, z)
        elif tag == TAG_TEMP:
            pending["temp"] = x
    row = finish()
    if row:
        yield row


def export(binary_path, csv_path=None, delete_binary=False, progress=None):
    """Convert a closed binary log to one analysis-ready CSV.

    Writes to ``.tmp`` and renames atomically. The binary is retained by
    default as the authoritative compact recovery copy.
    """
    if csv_path is None:
        csv_path = (binary_path[:-len(config.LOG_EXT)] + ".csv"
                    if binary_path.endswith(config.LOG_EXT)
                    else binary_path + ".csv")
    temp_path = csv_path + ".tmp"
    try:
        os.remove(temp_path)
    except OSError:
        pass

    header, time_slope, time_intercept, fitted = _fit_times(binary_path)
    imu = header.get("imu", {})
    a_scale = imu.get("accel_mg_per_lsb", 0.976) / 1000.0
    g_scale = imu.get("gyro_mdps_per_lsb", 70.0) / 1000.0
    temp_lsb = imu.get("temp_lsb_per_c", 256.0)
    temp_offset = imu.get("temp_offset_c", 25.0)
    baro = header.get("baro", {})
    calibration = baro.get("calibration", {})
    has_humidity = baro.get("has_humidity", False)
    pad_pressure = None
    last_altitude = None
    last_baro_us = None
    vertical_speed = 0.0
    rows = 0

    try:
        with open(binary_path, "rb") as source, open(temp_path, "w") as target:
            _header(source)
            target.write(",".join(FIELDS) + "\n")
            for record_type, payload in _records(source):
                generated = []
                if record_type == recfmt.REC_IMU_FIFO:
                    generated = _fifo_rows(payload, a_scale, g_scale,
                                           temp_lsb, temp_offset, time_slope,
                                           time_intercept, fitted)
                elif record_type == recfmt.REC_BARO:
                    t_us, adc_p, adc_t, adc_h = struct.unpack(recfmt.BARO_FMT,
                                                              payload)
                    row = {"t_us": t_us, "source": "barometer",
                           "adc_p": adc_p, "adc_t": adc_t, "adc_h": adc_h}
                    if calibration:
                        temp_c, t_fine = compensate_t(adc_t, calibration)
                        pressure = compensate_p(adc_p, t_fine, calibration)
                        if pad_pressure is None and pressure is not None and pressure > 0:
                            pad_pressure = pressure
                        altitude = (altitude_agl_m(pressure, pad_pressure)
                                    if pad_pressure is not None else None)
                        if altitude is not None and last_altitude is not None and last_baro_us is not None:
                            dt = (t_us - last_baro_us) / 1000000.0
                            if 0.001 <= dt <= 2.0:
                                raw_speed = (altitude - last_altitude) / dt
                                alpha = dt / (config.VERTICAL_SPEED_TAU_S + dt)
                                vertical_speed += alpha * (raw_speed - vertical_speed)
                        if altitude is not None:
                            last_altitude = altitude
                            last_baro_us = t_us
                        row.update({"baro_temp_c": temp_c,
                                    "pressure_pa": pressure,
                                    "altitude_agl_m": altitude,
                                    "vertical_speed_mps": vertical_speed})
                        if has_humidity:
                            row["baro_humidity_pct"] = compensate_h(
                                adc_h, t_fine, calibration)
                    generated = (row,)
                elif record_type == recfmt.REC_SEN65:
                    values = struct.unpack(recfmt.SEN65_FMT, payload)
                    words = values[1:9]
                    row = {"t_us": values[0], "source": "sen65",
                           "sen_status": values[9]}
                    names = ("pm1p0_ug_m3", "pm2p5_ug_m3", "pm4p0_ug_m3",
                             "pm10_ug_m3", "sen_rh_pct", "sen_temp_c",
                             "voc_index", "nox_index")
                    signed = (False, False, False, False, True, True, True, True)
                    scales = (10.0, 10.0, 10.0, 10.0, 100.0, 200.0, 10.0, 10.0)
                    for name, word, is_signed, scale in zip(names, words, signed, scales):
                        unknown = 0x7FFF if is_signed else 0xFFFF
                        row[name] = None if word == unknown else (
                            _signed(word) if is_signed else word) / scale
                    generated = (row,)
                elif record_type == recfmt.REC_SEN65_RAW:
                    values = struct.unpack(recfmt.SEN65_RAW_FMT, payload)
                    words = values[1:]
                    generated = ({"t_us": values[0], "source": "sen65_raw",
                                  "raw_rh_pct": None if words[0] == 0x7FFF else _signed(words[0]) / 100.0,
                                  "raw_temp_c": None if words[1] == 0x7FFF else _signed(words[1]) / 200.0,
                                  "raw_voc_ticks": None if words[2] == 0xFFFF else words[2],
                                  "raw_nox_ticks": None if words[3] == 0xFFFF else words[3]},)
                elif record_type == recfmt.REC_SEN65_NUMBER:
                    values = struct.unpack(recfmt.SEN65_NUMBER_FMT, payload)
                    names = ("nc0p5_per_cm3", "nc1p0_per_cm3", "nc2p5_per_cm3",
                             "nc4p0_per_cm3", "nc10_per_cm3")
                    row = {"t_us": values[0], "source": "sen65_number"}
                    for name, word in zip(names, values[1:]):
                        row[name] = None if word == 0xFFFF else word / 10.0
                    generated = (row,)
                elif record_type == recfmt.REC_MARKER:
                    t_us, tag = struct.unpack_from(recfmt.MARKER_HDR_FMT, payload, 0)
                    generated = ({"t_us": t_us, "source": "marker",
                                  "marker_tag": tag,
                                  "marker_name": recfmt.MARK_NAMES.get(tag, "unknown"),
                                  "marker_text": payload[recfmt.MARKER_HDR_LEN:].decode(
                                      "utf-8", "replace")},)
                elif record_type == recfmt.REC_CONFIG:
                    t_us = struct.unpack_from(recfmt.CONFIG_HDR_FMT, payload, 0)[0]
                    generated = ({"t_us": t_us, "source": "config",
                                  "config_json": payload[recfmt.CONFIG_HDR_LEN:].decode(
                                      "utf-8", "replace")},)
                elif record_type == recfmt.REC_TIMESYNC:
                    t_us, ticks = struct.unpack(recfmt.TIMESYNC_FMT, payload)
                    generated = ({"t_us": t_us, "source": "timesync",
                                  "imu_ticks": ticks},)

                for row in generated:
                    _write_row(target, row)
                    rows += 1
                    if rows % 1000 == 0:
                        target.flush()
                    if progress and rows % 10000 == 0:
                        progress(rows)

        if os.stat(temp_path)[6] <= len(",".join(FIELDS)) + 1:
            raise ExportError("CSV contains no data rows")
        try:
            os.remove(csv_path)
        except OSError:
            pass
        os.rename(temp_path, csv_path)
        if delete_binary:
            os.remove(binary_path)
        return {"csv_path": csv_path, "rows": rows,
                "bytes": os.stat(csv_path)[6], "binary_deleted": delete_binary,
                "timestamp_fit": fitted}
    except Exception:
        # Keep the original binary as the authoritative recoverable copy.
        raise
