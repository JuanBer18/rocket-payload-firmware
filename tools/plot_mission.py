"""Create a quick-look PNG from a dashboard-downloaded mission CSV."""

from pathlib import Path
import sys

import matplotlib.pyplot as plt
import pandas as pd


def plot_file(csv_path):
    csv_path = Path(csv_path)
    data = pd.read_csv(csv_path, low_memory=False)
    data["t_s"] = (data["t_us"] - data["t_us"].min()) / 1_000_000.0

    fig, axes = plt.subplots(4, 2, figsize=(14, 12), constrained_layout=True)
    panels = (
        ("Altitude", (("altitude_agl_m", "AGL altitude (m)"),
                      ("vertical_speed_mps", "vertical speed (m/s)"))),
        ("Acceleration", (("accel_mag_g", "magnitude (g)"),)),
        ("Particulate mass", (("pm1p0_ug_m3", "PM1.0"),
                              ("pm2p5_ug_m3", "PM2.5"),
                              ("pm4p0_ug_m3", "PM4.0"),
                              ("pm10_ug_m3", "PM10"))),
        ("Particle number", (("nc0p5_per_cm3", "0.5 um"),
                             ("nc1p0_per_cm3", "1.0 um"),
                             ("nc2p5_per_cm3", "2.5 um"))),
        ("Temperature", (("baro_temp_c", "BME280"),
                         ("sen_temp_c", "SEN65"))),
        ("Humidity", (("baro_humidity_pct", "BME280"),
                      ("sen_rh_pct", "SEN65"))),
        ("Gas indices", (("voc_index", "VOC index"),
                         ("nox_index", "NOx index"))),
        ("Raw gas signals", (("raw_voc_ticks", "raw VOC"),
                             ("raw_nox_ticks", "raw NOx"))),
    )

    for axis, (title, series) in zip(axes.flat, panels):
        for column, label in series:
            if column not in data:
                continue
            subset = data[["t_s", column]].dropna()
            if not subset.empty:
                axis.plot(subset["t_s"], subset[column], label=label,
                          linewidth=1.0)
        axis.set_title(title)
        axis.set_xlabel("time since log start (s)")
        axis.grid(True, alpha=0.25)
        if axis.lines:
            axis.legend(fontsize=8)

    output = csv_path.with_name(csv_path.stem + "_quicklook.png")
    fig.suptitle(csv_path.name)
    fig.savefig(output, dpi=160)
    plt.close(fig)
    print(output)
    return output


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python tools/plot_mission.py FLIGHT.csv")
    plot_file(sys.argv[1])
