#!/usr/bin/env python3
"""Create metrics, scientific plots, and experimental dead reckoning from a flight CSV.

Examples:
    python tools/analyze_flight.py flight_018
    python tools/analyze_flight.py "downloaded flight data/flight_018_analysis/flight_018.csv"
    python tools/analyze_flight.py

Short flight names are resolved automatically. With no argument, a file picker
opens so this script also works with the IDE Run button.

The inertial trajectory is an estimate only. With no magnetometer, GNSS, or
external position fixes, yaw and position drift are unavoidable. The tool uses
gyro-assisted attitude, gravity removal, and zero-velocity updates where the
payload appears stationary; it never presents the result as measured position.
"""

import argparse
import json
import math
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


G = 9.80665


def resolve_csv(value):
    """Resolve a CSV path or short name such as ``flight_003``."""
    supplied = Path(value).expanduser()
    candidates = [supplied]
    if not supplied.is_absolute():
        candidates.extend((PROJECT_ROOT / supplied,
                           PROJECT_ROOT / "downloaded flight data" / supplied))
    expanded = []
    for candidate in candidates:
        expanded.append(candidate)
        if not candidate.suffix:
            expanded.append(candidate.with_suffix(".csv"))
            expanded.append(candidate.parent / (candidate.name + "_analysis") /
                            (candidate.name + ".csv"))
    for candidate in expanded:
        if candidate.is_file() and candidate.suffix.lower() == ".csv":
            return candidate.resolve()
    wanted = supplied.stem + ".csv"
    data_root = PROJECT_ROOT / "downloaded flight data"
    matches = sorted(path.resolve() for path in data_root.rglob(wanted)
                     if path.is_file()) if data_root.exists() else []
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        preferred = [path for path in matches
                     if path.parent.name == supplied.stem + "_analysis"]
        if len(preferred) == 1:
            return preferred[0]
        raise FileNotFoundError("multiple CSV files match %r; provide the full path" % value)
    raise FileNotFoundError(
        "could not find decoded CSV %r; run tools\\process_flight.py first" % value)


def choose_csv():
    """Open a native file picker when launched from an IDE without arguments."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.update()
        selected = filedialog.askopenfilename(
            title="Select decoded payload flight CSV",
            initialdir=str(PROJECT_ROOT / "downloaded flight data"),
            filetypes=(("Flight CSV", "flight_*.csv"), ("CSV", "*.csv")))
        root.destroy()
        return Path(selected).resolve() if selected else None
    except Exception as exc:
        raise RuntimeError("file picker unavailable; provide a flight name") from exc


def default_output_for(csv_path):
    csv_path = Path(csv_path)
    expected = csv_path.stem + "_analysis"
    return csv_path.parent if csv_path.parent.name == expected else csv_path.parent / expected


def find_binary(csv_path):
    """Locate the original BIN beside or one level above an analysis folder."""
    csv_path = Path(csv_path)
    candidates = [csv_path.with_suffix(".bin"),
                  csv_path.parent.parent / (csv_path.stem + ".bin"),
                  PROJECT_ROOT / "downloaded flight data" / (csv_path.stem + ".bin")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _finite(series):
    return pd.to_numeric(series, errors="coerce").dropna()


def _stats(frame, columns):
    result = {}
    for column in columns:
        if column not in frame:
            continue
        values = _finite(frame[column])
        if len(values):
            result[column] = {
                "count": int(len(values)), "mean": float(values.mean()),
                "min": float(values.min()), "max": float(values.max()),
                "median": float(values.median()),
            }
    return result


def _source_rate(frame):
    if len(frame) < 2:
        return None
    span = (frame["t_us"].max() - frame["t_us"].min()) / 1e6
    return float((len(frame) - 1) / span) if span > 0 else None


def _downsample(frame, limit=15000):
    if len(frame) <= limit:
        return frame
    return frame.iloc[np.linspace(0, len(frame) - 1, limit).astype(int)]


def _finish_figure(fig, path):
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def _nice_bin_width(span, target_bins=25):
    """Return a readable 1/2/5-series altitude bin width."""
    if not np.isfinite(span) or span <= 0:
        return 1.0
    raw = max(span / target_bins, 0.1)
    exponent = 10.0 ** math.floor(math.log10(raw))
    fraction = raw / exponent
    factor = 1.0 if fraction <= 1.0 else 2.0 if fraction <= 2.0 else 5.0 if fraction <= 5.0 else 10.0
    return factor * exponent


def altitude_resolved_sen65(sen, baro, output):
    """Pair SEN65 samples with interpolated AGL altitude and build profiles."""
    sensor_columns = [
        "sen65_pm1p0", "sen65_pm2p5", "sen65_pm4p0", "sen65_pm10",
        "nc0p5_per_cm3", "nc1p0_per_cm3", "nc2p5_per_cm3",
        "nc4p0_per_cm3", "nc10_per_cm3", "sen65_voc", "sen65_nox",
        "sen65_temp", "sen65_rh",
    ]
    sensor_columns = [column for column in sensor_columns if column in sen]
    bcols = ["t_us", "altitude_agl_m"]
    if "vertical_speed_mps" in baro:
        bcols.append("vertical_speed_mps")
    b = baro[bcols].apply(pd.to_numeric, errors="coerce").dropna(
        subset=["t_us", "altitude_agl_m"])
    b = b.sort_values("t_us").drop_duplicates("t_us")
    profile = sen.copy().sort_values("t_us")
    profile["t_us"] = pd.to_numeric(profile["t_us"], errors="coerce")
    profile = profile.dropna(subset=["t_us"])
    if len(b) < 2 or not len(profile) or not sensor_columns:
        return None

    bt = b["t_us"].to_numpy(dtype=float)
    st = profile["t_us"].to_numpy(dtype=float)
    inside = (st >= bt[0]) & (st <= bt[-1])
    altitude = np.full(len(profile), np.nan)
    altitude[inside] = np.interp(st[inside], bt,
                                 b["altitude_agl_m"].to_numpy(dtype=float))
    profile["altitude_agl_m"] = altitude
    if "vertical_speed_mps" in b:
        speed_values = b["vertical_speed_mps"].to_numpy(dtype=float)
    else:
        seconds = bt / 1e6
        speed_values = np.gradient(b["altitude_agl_m"].to_numpy(dtype=float), seconds)
    vertical_speed = np.full(len(profile), np.nan)
    vertical_speed[inside] = np.interp(st[inside], bt, speed_values)
    profile["vertical_speed_mps"] = vertical_speed
    profile["flight_phase"] = np.where(
        vertical_speed > 0.5, "ascending",
        np.where(vertical_speed < -0.5, "descending", "level"))
    profile.to_csv(output / "sen65_altitude_samples.csv", index=False)

    valid = profile.dropna(subset=["altitude_agl_m"]).copy()
    if not len(valid):
        return None
    low = float(valid["altitude_agl_m"].min())
    high = float(valid["altitude_agl_m"].max())
    width = _nice_bin_width(high - low)
    edge0 = round(math.floor(low / width) * width, 9)
    edge1 = round(math.ceil(high / width) * width, 9)
    if edge1 <= edge0:
        edge1 = edge0 + width
    edges = np.arange(edge0, edge1 + width * 1.01, width)
    valid["altitude_bin"] = pd.cut(valid["altitude_agl_m"], edges,
                                    include_lowest=True, right=False)
    rows = []
    for (phase, interval), group in valid.groupby(
            ["flight_phase", "altitude_bin"], observed=True):
        row = {
            "flight_phase": phase,
            "altitude_bin_low_m": round(float(interval.left), 6),
            "altitude_bin_high_m": round(float(interval.right), 6),
            "altitude_bin_center_m": round(float((interval.left + interval.right) / 2), 6),
            "samples": int(len(group)),
        }
        for column in sensor_columns:
            values = pd.to_numeric(group[column], errors="coerce").dropna()
            row[column + "_median"] = float(values.median()) if len(values) else np.nan
            row[column + "_q25"] = float(values.quantile(0.25)) if len(values) else np.nan
            row[column + "_q75"] = float(values.quantile(0.75)) if len(values) else np.nan
        rows.append(row)
    binned = pd.DataFrame(rows)
    binned.to_csv(output / "sen65_altitude_binned.csv", index=False)

    panels = [
        (["sen65_pm1p0", "sen65_pm2p5", "sen65_pm4p0", "sen65_pm10"],
         "PM mass concentration (ug/m3)"),
        (["nc0p5_per_cm3", "nc1p0_per_cm3", "nc2p5_per_cm3",
          "nc4p0_per_cm3", "nc10_per_cm3"], "Number concentration (particles/cm3)"),
        (["sen65_voc", "sen65_nox"], "Processed gas index"),
        (["sen65_temp", "sen65_rh"], "Temperature (C) / RH (%)"),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(19, 9), sharey=True,
                             constrained_layout=True)
    phase_markers = {"ascending": "^", "descending": "v", "level": "o"}
    for axis, (columns, xlabel) in zip(axes, panels):
        columns = [column for column in columns if column in valid]
        for column in columns:
            for phase, marker in phase_markers.items():
                part = valid[valid["flight_phase"] == phase]
                values = pd.to_numeric(part[column], errors="coerce")
                mask = values.notna()
                if mask.any():
                    axis.scatter(values[mask], part.loc[mask, "altitude_agl_m"],
                                 s=8, alpha=0.14, marker=marker)
            med = binned[binned["flight_phase"] != "level"].sort_values(
                "altitude_bin_center_m")
            for phase, style in (("ascending", "-"), ("descending", "--")):
                phase_data = med[med["flight_phase"] == phase]
                value_column = column + "_median"
                if value_column in phase_data:
                    axis.plot(phase_data[value_column],
                              phase_data["altitude_bin_center_m"],
                              style, lw=1.4, label=column.replace("sen65_", ""))
        axis.set_xlabel(xlabel)
        axis.grid(True, alpha=0.25)
    axes[0].set_ylabel("Barometric altitude AGL (m)")
    axes[0].set_title("PM profile")
    axes[1].set_title("Particle-number profile")
    axes[2].set_title("Gas-index profile")
    axes[3].set_title("Temperature and humidity profile")
    for axis in axes:
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            unique = dict(zip(labels, handles))
            axis.legend(unique.values(), unique.keys(), fontsize=8)
    fig.suptitle("SEN65 altitude-resolved profiles (solid ascent, dashed descent)")
    _finish_figure(fig, output / "sen65_altitude_profiles.png")
    return {
        "paired_samples": int(len(valid)),
        "altitude_min_m": low,
        "altitude_max_m": high,
        "altitude_bin_width_m": float(width),
        "ascending_samples": int((valid["flight_phase"] == "ascending").sum()),
        "descending_samples": int((valid["flight_phase"] == "descending").sum()),
        "level_samples": int((valid["flight_phase"] == "level").sum()),
    }


def _reconstruct_imu_time(raw_seconds):
    """Interpolate within repeated FIFO timestamp groups, preserving anchors."""
    raw = np.asarray(raw_seconds, dtype=float)
    indices = np.arange(len(raw), dtype=float)
    anchors = np.r_[True, np.diff(raw) > 0]
    anchor_i = indices[anchors]
    anchor_t = raw[anchors]
    if len(anchor_i) < 2:
        return indices / 100.0 + raw[0]
    rebuilt = np.interp(indices, anchor_i, anchor_t)
    median_dt = np.median(np.diff(anchor_t) / np.maximum(1, np.diff(anchor_i)))
    last = int(anchor_i[-1])
    if last < len(raw) - 1:
        rebuilt[last + 1:] = anchor_t[-1] + median_dt * np.arange(1, len(raw) - last)
    return rebuilt


def dead_reckoning(imu, baro, rate_hz=100.0):
    required = ["t_us", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps"]
    source = imu[required].apply(pd.to_numeric, errors="coerce").dropna().copy()
    if len(source) < 100:
        raise ValueError("not enough complete accelerometer/gyro rows")

    raw_t = source["t_us"].to_numpy() / 1e6
    t = _reconstruct_imu_time(raw_t)
    grid = np.arange(t[0], t[-1], 1.0 / rate_hz)
    channels = {}
    for column in required[1:]:
        channels[column] = np.interp(grid, t, source[column].to_numpy())

    accel_g = np.column_stack([channels["ax_g"], channels["ay_g"], channels["az_g"]])
    gyro_dps = np.column_stack([channels["gx_dps"], channels["gy_dps"], channels["gz_dps"]])
    amag = np.linalg.norm(accel_g, axis=1)
    gnorm = np.linalg.norm(gyro_dps, axis=1)
    stationary_raw = (np.abs(amag - 1.0) < 0.035) & (gnorm < 2.5)
    window = max(5, int(rate_hz * 0.20))
    stationary = np.convolve(stationary_raw.astype(float),
                             np.ones(window) / window, mode="same") > 0.80

    initial = stationary & (grid <= grid[0] + 10.0)
    if initial.sum() < rate_hz * 0.25:
        initial = grid <= grid[0] + 2.0
    gyro_bias = np.median(gyro_dps[initial], axis=0)
    gyro = np.deg2rad(gyro_dps - gyro_bias)

    gravity_body = np.median(accel_g[initial], axis=0)
    gravity_body /= np.linalg.norm(gravity_body)
    roll = math.atan2(gravity_body[1], gravity_body[2])
    pitch = math.atan2(-gravity_body[0],
                       math.sqrt(gravity_body[1] ** 2 + gravity_body[2] ** 2))
    yaw = 0.0
    euler = np.zeros((len(grid), 3))
    dt = 1.0 / rate_hz
    correction = 0.012

    for index in range(len(grid)):
        p, q, r = gyro[index]
        cp = max(0.05, abs(math.cos(pitch)))
        roll_rate = p + math.sin(roll) * math.tan(pitch) * q + math.cos(roll) * math.tan(pitch) * r
        pitch_rate = math.cos(roll) * q - math.sin(roll) * r
        yaw_rate = math.sin(roll) / cp * q + math.cos(roll) / cp * r
        roll += roll_rate * dt
        pitch += pitch_rate * dt
        yaw += yaw_rate * dt
        if 0.85 < amag[index] < 1.15:
            ar = math.atan2(accel_g[index, 1], accel_g[index, 2])
            ap = math.atan2(-accel_g[index, 0],
                            math.hypot(accel_g[index, 1], accel_g[index, 2]))
            roll = (1.0 - correction) * roll + correction * ar
            pitch = (1.0 - correction) * pitch + correction * ap
        euler[index] = (roll, pitch, yaw)

    sr, cr = np.sin(euler[:, 0]), np.cos(euler[:, 0])
    sp, cp = np.sin(euler[:, 1]), np.cos(euler[:, 1])
    sy, cy = np.sin(euler[:, 2]), np.cos(euler[:, 2])
    ax, ay, az = accel_g[:, 0], accel_g[:, 1], accel_g[:, 2]
    world = np.column_stack([
        cy * cp * ax + (cy * sp * sr - sy * cr) * ay + (cy * sp * cr + sy * sr) * az,
        sy * cp * ax + (sy * sp * sr + cy * cr) * ay + (sy * sp * cr - cy * sr) * az,
        -sp * ax + cp * sr * ay + cp * cr * az,
    ]) * G
    linear = world - np.array([0.0, 0.0, G])
    if stationary.sum() >= 10:
        linear -= np.median(linear[stationary], axis=0)

    # Suppress sensor noise but preserve deliberate motion.
    smooth_n = max(3, int(rate_hz * 0.05))
    kernel = np.ones(smooth_n) / smooth_n
    for axis in range(3):
        linear[:, axis] = np.convolve(linear[:, axis], kernel, mode="same")

    velocity = np.zeros_like(linear)
    position = np.zeros_like(linear)
    for index in range(1, len(grid)):
        velocity[index] = velocity[index - 1] + 0.5 * (linear[index - 1] + linear[index]) * dt
        if stationary[index]:
            velocity[index] = 0.0
        position[index] = position[index - 1] + 0.5 * (velocity[index - 1] + velocity[index]) * dt

    baro_valid = baro[["t_us", "altitude_agl_m"]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(baro_valid) >= 2:
        baro_z = np.interp(grid, baro_valid["t_us"] / 1e6,
                           baro_valid["altitude_agl_m"])
    else:
        baro_z = np.full(len(grid), np.nan)

    return pd.DataFrame({
        "time_s": grid - grid[0],
        "x_inertial_m": position[:, 0], "y_inertial_m": position[:, 1],
        "z_inertial_m": position[:, 2], "z_baro_agl_m": baro_z,
        "vx_mps": velocity[:, 0], "vy_mps": velocity[:, 1], "vz_mps": velocity[:, 2],
        "linear_ax_mps2": linear[:, 0], "linear_ay_mps2": linear[:, 1],
        "linear_az_mps2": linear[:, 2], "roll_deg": np.rad2deg(euler[:, 0]),
        "pitch_deg": np.rad2deg(euler[:, 1]), "yaw_relative_deg": np.rad2deg(euler[:, 2]),
        "stationary": stationary.astype(int),
    }), gyro_bias


def analyze(csv_path, output_dir, binary_path=None):
    csv_path = Path(csv_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    data = pd.read_csv(csv_path, low_memory=False)
    data["t_us"] = pd.to_numeric(data["t_us"], errors="coerce")
    t0 = data["t_us"].min()
    data["time_s"] = (data["t_us"] - t0) / 1e6
    imu = data[data["source"] == "imu"].copy()
    baro = data[data["source"] == "baro"].copy()
    sen = data[data["source"].isin(["sen65", "sen65_raw", "sen65_number"])].copy()
    sen = sen.groupby("t_us", as_index=False).first()
    sen["time_s"] = (sen["t_us"] - t0) / 1e6

    duration = (data["t_us"].max() - t0) / 1e6
    metrics = {
        "input": {"csv": str(csv_path.resolve()),
                  "binary": str(Path(binary_path).resolve()) if binary_path else None},
        "duration_s": float(duration),
        "duration_hms": "%02d:%02d:%05.2f" %
                        (int(duration // 3600), int(duration % 3600 // 60), duration % 60),
        "storage": {"binary_bytes": Path(binary_path).stat().st_size if binary_path else None,
                    "binary_mib": (Path(binary_path).stat().st_size / 1048576.0
                                   if binary_path else None),
                    "csv_bytes": csv_path.stat().st_size,
                    "csv_mib": csv_path.stat().st_size / 1048576.0,
                    "average_binary_kib_s": (Path(binary_path).stat().st_size / 1024.0 / duration
                                             if binary_path and duration else None)},
        "rows": {str(k): int(v) for k, v in data["source"].value_counts().items()},
        "rates_hz": {"imu_rows": _source_rate(imu), "barometer": _source_rate(baro),
                     "sen65_measurements": _source_rate(sen)},
        "imu": _stats(imu, ["a_mag_g", "ax_g", "ay_g", "az_g",
                                  "gx_dps", "gy_dps", "gz_dps"]),
        "barometer": _stats(baro, ["altitude_agl_m", "pressure_pa",
                                    "baro_temp_c", "humidity_pct"]),
        "sen65": _stats(sen, ["sen65_pm1p0", "sen65_pm2p5", "sen65_pm4p0",
                               "sen65_pm10", "sen65_rh", "sen65_temp",
                               "sen65_voc", "sen65_nox", "nc0p5_per_cm3",
                               "nc1p0_per_cm3", "nc2p5_per_cm3",
                               "nc4p0_per_cm3", "nc10_per_cm3"]),
        "warnings": [
            "Accelerometer-only position is experimental: yaw is relative and position drifts without GNSS/magnetometer/external fixes.",
            "Repeated FIFO timestamps were interpolated before 100 Hz dead reckoning.",
            "Barometric altitude is relative to the initial pressure, not sea-level altitude.",
            "SEN65 altitude profiles interpolate barometric altitude to each approximately 1 Hz air sample; rapid ascent, airflow lag, and the sensor response time can smear the vertical profile.",
        ],
    }
    peak_accel = imu.loc[pd.to_numeric(imu["a_mag_g"], errors="coerce").idxmax()]
    max_altitude = baro.loc[pd.to_numeric(baro["altitude_agl_m"], errors="coerce").idxmax()]
    min_altitude = baro.loc[pd.to_numeric(baro["altitude_agl_m"], errors="coerce").idxmin()]
    metrics["key_events"] = {
        "peak_acceleration": {"value_g": float(peak_accel["a_mag_g"]),
                              "time_s": float((peak_accel["t_us"] - t0) / 1e6)},
        "maximum_baro_altitude": {"value_m": float(max_altitude["altitude_agl_m"]),
                                  "time_s": float((max_altitude["t_us"] - t0) / 1e6)},
        "minimum_baro_altitude": {"value_m": float(min_altitude["altitude_agl_m"]),
                                  "time_s": float((min_altitude["t_us"] - t0) / 1e6)},
    }

    if binary_path:
        from tools.decode import LogReader
        reader = LogReader(binary_path).open()
        for _record in reader.records():
            pass
        metrics["integrity"] = {"truncated_tail": bool(reader.truncated),
                                "resyncs": int(reader.resyncs),
                                "skipped_bytes": int(reader.skipped_bytes),
                                "header_ok": bool(reader.header_ok)}
        metrics["log_header"] = {
            "mission_mode": reader.header.get("mission", {}).get("mission_mode"),
            "auto_stop": reader.header.get("mission", {}).get("auto_stop"),
            "imu_initial_odr_hz": reader.header.get("imu", {}).get("odr_hz"),
        }
        if reader.truncated:
            metrics["warnings"].append(
                "The BIN ends mid-record, consistent with power loss; earlier records decoded normally.")

    # Requested paired altitude and acceleration plot.
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True, constrained_layout=True)
    b = _downsample(baro)
    i = _downsample(imu)
    axes[0].plot(b["time_s"], b["altitude_agl_m"], color="#2563eb", lw=1.2)
    axes[0].axhline(0, color="black", lw=0.7, alpha=0.4)
    axes[0].set_ylabel("Altitude AGL (m)"); axes[0].set_title("Barometric altitude")
    axes[1].plot(i["time_s"], i["a_mag_g"], color="#dc2626", lw=0.6)
    axes[1].axhline(1, color="black", lw=0.7, alpha=0.4)
    axes[1].set_ylabel("Acceleration magnitude (g)"); axes[1].set_xlabel("Time since log start (s)")
    for axis in axes: axis.grid(True, alpha=0.25)
    _finish_figure(fig, output / "altitude_and_acceleration.png")

    fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True, constrained_layout=True)
    axes[0].plot(b["time_s"], b["pressure_pa"] / 100.0, color="#0f766e")
    axes[0].set_ylabel("Pressure (hPa)")
    axes[1].plot(b["time_s"], b["baro_temp_c"], label="BME280 temperature", color="#ea580c")
    if "humidity_pct" in b: axes[1].plot(b["time_s"], b["humidity_pct"], label="BME280 RH", color="#0891b2")
    axes[1].set_ylabel("Temperature / RH"); axes[1].legend()
    axes[2].plot(b["time_s"], b["altitude_agl_m"], color="#2563eb")
    axes[2].set_ylabel("Altitude AGL (m)"); axes[2].set_xlabel("Time since log start (s)")
    for axis in axes: axis.grid(True, alpha=0.25)
    _finish_figure(fig, output / "barometer_analysis.png")

    fig, axes = plt.subplots(5, 1, figsize=(14, 15), sharex=True, constrained_layout=True)
    for c, label in [("sen65_pm1p0", "PM1.0"), ("sen65_pm2p5", "PM2.5"),
                     ("sen65_pm4p0", "PM4.0"), ("sen65_pm10", "PM10")]:
        axes[0].plot(sen["time_s"], sen[c], label=label)
    axes[0].set_ylabel("PM mass (ug/m3)"); axes[0].legend(ncol=4)
    for c, label in [("nc0p5_per_cm3", ">=0.5um"), ("nc1p0_per_cm3", ">=1.0um"),
                     ("nc2p5_per_cm3", ">=2.5um"), ("nc4p0_per_cm3", ">=4.0um"),
                     ("nc10_per_cm3", ">=10um")]:
        axes[1].plot(sen["time_s"], sen[c], label=label)
    axes[1].set_ylabel("Particles/cm3"); axes[1].legend(ncol=3)
    axes[2].plot(sen["time_s"], sen["sen65_voc"], label="VOC index")
    axes[2].plot(sen["time_s"], sen["sen65_nox"], label="NOx index")
    axes[2].set_ylabel("Gas index"); axes[2].legend()
    axes[3].plot(sen["time_s"], sen["sen65_temp"], label="Temperature (C)")
    axes[3].plot(sen["time_s"], sen["sen65_rh"], label="RH (%)")
    axes[3].set_ylabel("Temperature / RH"); axes[3].legend()
    axes[4].plot(sen["time_s"], sen.get("raw_voc_ticks"), label="Raw VOC")
    axes[4].plot(sen["time_s"], sen.get("raw_nox_ticks"), label="Raw NOx")
    axes[4].set_ylabel("Raw ticks"); axes[4].set_xlabel("Time since log start (s)"); axes[4].legend()
    for axis in axes: axis.grid(True, alpha=0.25)
    _finish_figure(fig, output / "sen65_analysis.png")

    altitude_profile = altitude_resolved_sen65(sen, baro, output)
    metrics["sen65_altitude_profile"] = altitude_profile

    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True, constrained_layout=True)
    for c in ("ax_g", "ay_g", "az_g"):
        axes[0].plot(i["time_s"], i[c], lw=0.6, label=c)
    axes[0].plot(i["time_s"], i["a_mag_g"], lw=0.8, color="black", label="magnitude")
    axes[0].set_ylabel("Acceleration (g)"); axes[0].legend(ncol=4)
    for c in ("gx_dps", "gy_dps", "gz_dps"):
        axes[1].plot(i["time_s"], i[c], lw=0.6, label=c)
    axes[1].set_ylabel("Angular rate (deg/s)"); axes[1].set_xlabel("Time since log start (s)"); axes[1].legend(ncol=3)
    for axis in axes: axis.grid(True, alpha=0.25)
    _finish_figure(fig, output / "imu_motion.png")

    dr, gyro_bias = dead_reckoning(imu, baro)
    dr.to_csv(output / "dead_reckoning_experimental.csv", index=False)
    metrics["dead_reckoning"] = {
        "method": "100 Hz complementary tilt + gyro integration + gravity removal + ZUPT",
        "gyro_bias_dps": [float(x) for x in gyro_bias],
        "stationary_fraction": float(dr["stationary"].mean()),
        "endpoint_inertial_m": [float(dr[c].iloc[-1]) for c in
                                ("x_inertial_m", "y_inertial_m", "z_inertial_m")],
        "max_horizontal_radius_m": float(np.hypot(dr["x_inertial_m"], dr["y_inertial_m"]).max()),
    }
    step = max(1, len(dr) // 12000)
    dp = dr.iloc[::step]
    fig = plt.figure(figsize=(14, 10), constrained_layout=True)
    fig.suptitle("Experimental dead reckoning - diagnostic only, not ground truth",
                 color="#b91c1c", fontsize=14)
    ax3 = fig.add_subplot(2, 2, 1, projection="3d")
    ax3.plot(dp["x_inertial_m"], dp["y_inertial_m"], dp["z_baro_agl_m"], lw=0.8)
    ax3.set_title("Experimental 3D path (barometric Z)")
    ax3.set_xlabel("X inertial (m)"); ax3.set_ylabel("Y inertial (m)"); ax3.set_zlabel("Barometric AGL (m)")
    ax2 = fig.add_subplot(2, 2, 2)
    ax2.plot(dp["x_inertial_m"], dp["y_inertial_m"], lw=0.8)
    ax2.scatter([0], [0], c="green", label="start"); ax2.scatter([dp["x_inertial_m"].iloc[-1]], [dp["y_inertial_m"].iloc[-1]], c="red", label="end")
    ax2.set_title("Experimental horizontal path (arbitrary heading)"); ax2.set_xlabel("X (m)"); ax2.set_ylabel("Y (m)"); ax2.axis("equal"); ax2.legend(); ax2.grid(True, alpha=0.25)
    axz = fig.add_subplot(2, 1, 2)
    axz.plot(dp["time_s"], dp["z_inertial_m"], label="accelerometer-only Z", alpha=0.8)
    axz.plot(dp["time_s"], dp["z_baro_agl_m"], label="barometric Z (preferred)", lw=1.2)
    axz.set_title("Vertical estimate comparison"); axz.set_xlabel("Time (s)"); axz.set_ylabel("Height (m)"); axz.legend(); axz.grid(True, alpha=0.25)
    _finish_figure(fig, output / "dead_reckoning_experimental.png")

    (output / "summary.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    binary_bytes_text = (str(metrics["storage"]["binary_bytes"])
                         if binary_path else "not supplied")
    binary_rate_text = ("%.2f KiB/s" % metrics["storage"]["average_binary_kib_s"]
                        if binary_path and duration else "not available")
    lines = [
        "FLIGHT ANALYSIS SUMMARY", "=" * 60,
        "Duration: %s (%.2f s)" % (metrics["duration_hms"], duration),
        "BIN storage: %s bytes" % binary_bytes_text,
        "Average BIN data rate: %s" % binary_rate_text,
        "CSV storage: %s bytes" % metrics["storage"]["csv_bytes"],
        "IMU rows: %d; barometer rows: %d; SEN65 measurement times: %d" %
        (len(imu), len(baro), len(sen)),
        "Altitude AGL min/max: %.2f / %.2f m" %
        (metrics["barometer"]["altitude_agl_m"]["min"], metrics["barometer"]["altitude_agl_m"]["max"]),
        "Peak acceleration magnitude: %.3f g" % metrics["imu"]["a_mag_g"]["max"],
        "Peak acceleration time: %.2f s" % metrics["key_events"]["peak_acceleration"]["time_s"],
        "SEN65 altitude profile: %s" %
        ("%d paired samples, %.2f to %.2f m AGL, %.2f m bins" %
         (altitude_profile["paired_samples"], altitude_profile["altitude_min_m"],
          altitude_profile["altitude_max_m"], altitude_profile["altitude_bin_width_m"])
         if altitude_profile else "not available"),
        "Dead-reckoning endpoint (NOT ground truth): %.1f, %.1f, %.1f m" %
        tuple(metrics["dead_reckoning"]["endpoint_inertial_m"]),
        "", "WARNINGS:",
    ] + ["- " + warning for warning in metrics["warnings"]]
    (output / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    pm25 = metrics["sen65"].get("sen65_pm2p5", {})
    voc = metrics["sen65"].get("sen65_voc", {})
    mode = metrics.get("log_header", {}).get("mission_mode") or "unknown"
    binary_size_text = ("%.2f MiB" % metrics["storage"]["binary_mib"]
                        if binary_path else "not supplied")
    binary_rate_markdown = ("%.2f KiB/s" % metrics["storage"]["average_binary_kib_s"]
                            if binary_path and duration else "not available")
    integrity = metrics.get("integrity")
    if integrity:
        if integrity["truncated_tail"]:
            integrity_text = ("The BIN ends in an incomplete record, usually caused by power removal "
                              "without a safe stop. Earlier valid records were retained.")
        else:
            integrity_text = ("The BIN has a complete final record. Decoder resynchronisations: "
                              "**%d**; skipped bytes: **%d**." %
                              (integrity["resyncs"], integrity["skipped_bytes"]))
    else:
        integrity_text = "No original BIN was supplied, so binary integrity was not checked."
    rh_stats = metrics["sen65"].get("sen65_rh", {})
    temp_stats = metrics["sen65"].get("sen65_temp", {})
    environment_text = (
        "SEN65 RH ranged from **%.2f%% to %.2f%%** and temperature from "
        "**%.2f C to %.2f C**. Allow for sensor thermal equilibration and "
        "possible condensation when interpreting the profile." %
        (rh_stats.get("min", float("nan")), rh_stats.get("max", float("nan")),
         temp_stats.get("min", float("nan")), temp_stats.get("max", float("nan"))))
    markdown = [
        "# Flight analysis",
        "",
        "## Simple metrics",
        "",
        "- Duration: **%s** (%.2f seconds)" % (metrics["duration_hms"], duration),
        "- Original BIN: **%s**; decoded CSV: **%.2f MiB**" %
        (binary_size_text, metrics["storage"]["csv_mib"]),
        "- Average binary logging rate: **%s**" % binary_rate_markdown,
        "- Mode recorded in header: **%s**" % mode,
        "- Rows: **%d IMU**, **%d barometer**, **%d SEN65 measurement times**" %
        (len(imu), len(baro), len(sen)),
        "- Effective decoded rates: IMU rows %.1f Hz, barometer %.2f Hz, SEN65 %.3f Hz" %
        (metrics["rates_hz"]["imu_rows"], metrics["rates_hz"]["barometer"],
         metrics["rates_hz"]["sen65_measurements"]),
        "",
        "## Main observations",
        "",
        "- Peak acceleration magnitude was **%.3f g at %.2f s**." %
        (metrics["key_events"]["peak_acceleration"]["value_g"],
         metrics["key_events"]["peak_acceleration"]["time_s"]),
        "- Barometric altitude ranged from **%.2f m to %.2f m AGL**. The reference is the initial pressure, not sea level." %
        (metrics["barometer"]["altitude_agl_m"]["min"],
         metrics["barometer"]["altitude_agl_m"]["max"]),
        "- Maximum barometric altitude occurred at **%.2f s**; minimum occurred at **%.2f s**." %
        (metrics["key_events"]["maximum_baro_altitude"]["time_s"],
         metrics["key_events"]["minimum_baro_altitude"]["time_s"]),
        "- PM2.5 averaged **%.2f ug/m3** and peaked at **%.2f ug/m3**." %
        (pm25.get("mean", float("nan")), pm25.get("max", float("nan"))),
        "- VOC index averaged **%.1f** and peaked at **%.1f**. NOx remained close to its lower processed-index boundary." %
        (voc.get("mean", float("nan")), voc.get("max", float("nan"))),
        "- The altitude-resolved SEN65 profile contains **%s**." %
        ("%d paired air samples from %.2f to %.2f m AGL, grouped into %.2f m altitude bins" %
         (altitude_profile["paired_samples"], altitude_profile["altitude_min_m"],
          altitude_profile["altitude_max_m"], altitude_profile["altitude_bin_width_m"])
         if altitude_profile else "no usable overlapping SEN65 and barometer samples"),
        "",
        "## Integrity and interpretation",
        "",
        "- " + integrity_text,
        "- The barometric altitude span was **%.2f m AGL**. It is a pressure-derived relative height, so airflow over the pressure port and changing weather can also move it." %
        (metrics["barometer"]["altitude_agl_m"]["max"] -
         metrics["barometer"]["altitude_agl_m"]["min"]),
        "- " + environment_text,
        "",
        "## Dead reckoning limitation",
        "",
        "The experimental inertial endpoint is **(%.1f, %.1f, %.1f) m**. This is a diagnostic estimate, not measured position: accelerometer/gyro double integration accumulates bias and yaw is unobservable without an external reference. The 3D figure therefore uses barometric altitude for Z and labels horizontal heading as arbitrary. Reliable 3D tracking needs GNSS or another position reference plus a stronger attitude/filter pipeline." %
        tuple(metrics["dead_reckoning"]["endpoint_inertial_m"]),
    ]
    (output / "analysis_report.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?",
                        help="decoded CSV path or short name, e.g. flight_003")
    parser.add_argument("--binary", help="original BIN for storage/integrity metrics")
    parser.add_argument("--output", help="output directory")
    args = parser.parse_args(argv)
    try:
        csv_path = resolve_csv(args.csv) if args.csv else choose_csv()
        if csv_path is None:
            print("No CSV selected; analysis cancelled.")
            return 0
    except (FileNotFoundError, RuntimeError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        print("Example: python tools\\analyze_flight.py flight_003", file=sys.stderr)
        return 2
    binary = Path(args.binary).resolve() if args.binary else find_binary(csv_path)
    output = Path(args.output).resolve() if args.output else default_output_for(csv_path)
    print("CSV:    %s" % csv_path)
    print("BIN:    %s" % (binary if binary else "not found (integrity metrics unavailable)"))
    print("Output: %s" % output)
    metrics = analyze(csv_path, output, binary)
    print("Analysis written to %s" % Path(output).resolve())
    print("Duration %s; peak acceleration %.3f g" %
          (metrics["duration_hms"], metrics["imu"]["a_mag_g"]["max"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
