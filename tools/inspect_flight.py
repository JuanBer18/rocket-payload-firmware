#!/usr/bin/env python3
"""Interactive desktop inspector for zooming and cropping decoded flights.

Examples:
    python tools/inspect_flight.py flight_021
    python tools/inspect_flight.py "downloaded flight data/flight_021_analysis/flight_021.csv"
    python tools/inspect_flight.py

Use the Matplotlib toolbar to zoom or pan every time-aligned plot. Turn on
"Select crop", drag across the bottom plot, then export the selected interval.
The original CSV is never changed and original ``t_us`` timestamps are kept.
"""

import argparse
import math
from pathlib import Path
import sys

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector
import numpy as np
import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTERNAL_TIME = "_inspect_time_s"


PRESETS = {
    "Overview": (
        ("Altitude", "AGL altitude (m)",
         (("altitude_agl_m", "Altitude", "#2563eb", "baro"),)),
        ("Acceleration", "Magnitude (g)",
         (("a_mag_g", "G magnitude", "#dc2626", "imu"),)),
        ("Pressure", "Pressure (hPa)",
         (("pressure_hpa", "Pressure", "#0f766e", "baro"),)),
        ("Particulate matter", "Mass (ug/m3)",
         (("sen65_pm1p0", "PM1.0", "#2563eb", None),
          ("sen65_pm2p5", "PM2.5", "#dc2626", None),
          ("sen65_pm4p0", "PM4.0", "#16a34a", None),
          ("sen65_pm10", "PM10", "#d97706", None))),
        ("Gas indices", "Index", (("sen65_voc", "VOC", "#7c3aed", None),
                                    ("sen65_nox", "NOx", "#111827", None))),
    ),
    "Motion": (
        ("Acceleration magnitude", "Magnitude (g)",
         (("a_mag_g", "Magnitude", "#111827", "imu"),)),
        ("Acceleration axes", "Acceleration (g)",
         (("ax_g", "X", "#dc2626", "imu"),
          ("ay_g", "Y", "#16a34a", "imu"),
          ("az_g", "Z", "#2563eb", "imu"))),
        ("Angular rates", "Rate (deg/s)",
         (("gx_dps", "X", "#dc2626", "imu"),
          ("gy_dps", "Y", "#16a34a", "imu"),
          ("gz_dps", "Z", "#2563eb", "imu"))),
        ("Barometric altitude", "AGL altitude (m)",
         (("altitude_agl_m", "Altitude", "#2563eb", "baro"),)),
    ),
    "Environment": (
        ("Barometric altitude", "AGL altitude (m)",
         (("altitude_agl_m", "Altitude", "#2563eb", "baro"),)),
        ("Pressure", "Pressure (hPa)",
         (("pressure_hpa", "Pressure", "#0f766e", "baro"),)),
        ("Temperature", "Temperature (C)",
         (("baro_temp_c", "BME280", "#ea580c", "baro"),
          ("sen65_temp", "SEN65", "#7c3aed", None))),
        ("Humidity", "Relative humidity (%)",
         (("humidity_pct", "BME280", "#0891b2", "baro"),
          ("sen65_rh", "SEN65", "#2563eb", None))),
    ),
    "Air quality": (
        ("Particulate mass", "Mass (ug/m3)",
         (("sen65_pm1p0", "PM1.0", "#2563eb", None),
          ("sen65_pm2p5", "PM2.5", "#dc2626", None),
          ("sen65_pm4p0", "PM4.0", "#16a34a", None),
          ("sen65_pm10", "PM10", "#d97706", None))),
        ("Particle number", "Particles/cm3",
         (("nc0p5_per_cm3", ">=0.5 um", "#2563eb", None),
          ("nc1p0_per_cm3", ">=1.0 um", "#dc2626", None),
          ("nc2p5_per_cm3", ">=2.5 um", "#16a34a", None),
          ("nc4p0_per_cm3", ">=4.0 um", "#d97706", None),
          ("nc10_per_cm3", ">=10 um", "#7c3aed", None))),
        ("Gas indices", "Index", (("sen65_voc", "VOC", "#7c3aed", None),
                                    ("sen65_nox", "NOx", "#111827", None))),
        ("Raw gas signals", "Raw ticks",
         (("raw_voc_ticks", "VOC raw", "#7c3aed", None),
          ("raw_nox_ticks", "NOx raw", "#111827", None))),
        ("SEN65 temperature and humidity", "C / %",
         (("sen65_temp", "Temperature", "#ea580c", None),
          ("sen65_rh", "Humidity", "#0891b2", None))),
    ),
}


def signal_metadata():
    """Return known signal metadata keyed by decoded CSV column name."""
    result = {}
    for panels in PRESETS.values():
        for _title, ylabel, series_list in panels:
            for column, label, color, source in series_list:
                result.setdefault(column, (label, ylabel, color, source))
    return result


SIGNAL_META = signal_metadata()
DEFAULT_SIGNALS = ("altitude_agl_m", "a_mag_g", "pressure_hpa",
                   "sen65_voc", "sen65_pm1p0", "sen65_pm2p5",
                   "sen65_pm4p0", "sen65_pm10")


def resolve_csv(value):
    """Resolve a CSV path or a short flight name such as flight_021."""
    supplied = Path(value).expanduser()
    candidates = [supplied]
    if not supplied.is_absolute():
        candidates.extend([PROJECT_ROOT / supplied,
                           PROJECT_ROOT / "downloaded flight data" / supplied])
    expanded = []
    for candidate in candidates:
        expanded.append(candidate)
        if not candidate.suffix:
            expanded.append(candidate.with_suffix(".csv"))
            expanded.append(candidate.parent / (candidate.name + "_analysis") /
                            (candidate.name + ".csv"))
        elif candidate.suffix.lower() == ".bin":
            expanded.append(candidate.parent / (candidate.stem + "_analysis") /
                            (candidate.stem + ".csv"))
    for candidate in expanded:
        if candidate.is_file() and candidate.suffix.lower() == ".csv":
            return candidate.resolve()
    wanted = supplied.stem + ".csv"
    data_root = PROJECT_ROOT / "downloaded flight data"
    matches = list(data_root.rglob(wanted)) if data_root.exists() else []
    matches = [path for path in matches if path.is_file()]
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        raise FileNotFoundError("multiple CSV files match %r; provide a full path" % value)
    raise FileNotFoundError(
        "could not find a decoded CSV for %r; run tools\\process_flight.py first" % value)


def load_flight_csv(path):
    path = Path(path)
    data = pd.read_csv(path, low_memory=False)
    required = {"t_us", "source"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError("CSV is missing required columns: %s" % ", ".join(sorted(missing)))
    timestamps = pd.to_numeric(data["t_us"], errors="coerce")
    if not timestamps.notna().any():
        raise ValueError("CSV has no valid t_us timestamps")
    t0 = float(timestamps.min())
    data[INTERNAL_TIME] = (timestamps - t0) / 1_000_000.0
    if "pressure_pa" in data.columns:
        data["pressure_hpa"] = pd.to_numeric(data["pressure_pa"],
                                              errors="coerce") / 100.0
    return data, t0


def crop_frame(data, start_s, end_s):
    start_s, end_s = sorted((float(start_s), float(end_s)))
    times = pd.to_numeric(data[INTERNAL_TIME], errors="coerce")
    cropped = data.loc[times.between(start_s, end_s, inclusive="both")].copy()
    return cropped.drop(columns=[INTERNAL_TIME, "pressure_hpa"], errors="ignore")


def decimate_xy(x_values, y_values, max_points=16000):
    """Keep min/max envelopes so brief peaks survive display downsampling."""
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if not len(x):
        return x, y
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    if len(x) <= max_points:
        return x, y
    chunk = max(1, int(math.ceil(len(x) / (max_points / 2.0))))
    indices = []
    for start in range(0, len(x), chunk):
        stop = min(len(x), start + chunk)
        segment = y[start:stop]
        indices.extend((start + int(np.argmin(segment)),
                        start + int(np.argmax(segment))))
    indices = np.asarray(sorted(set(indices)), dtype=int)
    return x[indices], y[indices]


def interval_statistics(data, columns, start_s, end_s):
    """Calculate MATLAB-style summary statistics for a time interval."""
    start_s, end_s = sorted((float(start_s), float(end_s)))
    mask = pd.to_numeric(data[INTERNAL_TIME], errors="coerce").between(
        start_s, end_s, inclusive="both")
    rows = []
    for column in columns:
        if column not in data.columns:
            continue
        values = pd.to_numeric(data.loc[mask, column], errors="coerce").dropna()
        if values.empty:
            continue
        rows.append((column, int(values.size), float(values.min()),
                     float(values.max()), float(values.mean()),
                     float(values.std(ddof=0))))
    return rows


class FlightInspector:
    def __init__(self, root):
        self.root = root
        self.root.title("Payload Mission Time Inspector")
        self.root.geometry("1400x900")
        self.root.minsize(900, 650)
        self.csv_path = None
        self.data = None
        self.t0_us = None
        self.full_range = (0.0, 1.0)
        self.axes = []
        self.selector = None
        self.selection_enabled = False
        self.selection_patches = []
        self.cursor_lines = []
        self.cursor_annotations = []
        self.cursor_times = [None, None]
        self.cursor_slot = 0
        self.series_cache = {}

        self.preset_var = tk.StringVar(value="Overview")
        self.start_var = tk.StringVar(value="0.000")
        self.end_var = tk.StringVar(value="0.000")
        self.file_var = tk.StringVar(value="No flight loaded")
        self.status_var = tk.StringVar(value="Open a decoded flight CSV to begin.")
        self.axes_count_var = tk.IntVar(value=4)
        self.cursor_var = tk.BooleanVar(value=False)
        self.grid_var = tk.BooleanVar(value=True)
        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Open flight", command=self.open_dialog).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.file_var).pack(side=tk.LEFT, padx=(8, 18))
        ttk.Label(top, text="View:").pack(side=tk.LEFT)
        preset = ttk.Combobox(top, textvariable=self.preset_var,
                             values=list(PRESETS) + ["Custom"], state="readonly", width=13)
        preset.pack(side=tk.LEFT, padx=(4, 12))
        preset.bind("<<ComboboxSelected>>", self._preset_changed)
        self.select_button = ttk.Button(top, text="Select crop: OFF",
                                        command=self.toggle_selection)
        self.select_button.pack(side=tk.LEFT)

        crop = ttk.Frame(self.root, padding=(6, 0, 6, 5))
        crop.pack(fill=tk.X)
        ttk.Label(crop, text="Crop start (s):").pack(side=tk.LEFT)
        ttk.Entry(crop, textvariable=self.start_var, width=11).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(crop, text="end (s):").pack(side=tk.LEFT)
        ttk.Entry(crop, textvariable=self.end_var, width=11).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Button(crop, text="Use visible window",
                   command=self.use_visible_window).pack(side=tk.LEFT, padx=2)
        ttk.Button(crop, text="Zoom to crop",
                   command=self.zoom_to_crop).pack(side=tk.LEFT, padx=2)
        ttk.Button(crop, text="Full view", command=self.full_view).pack(side=tk.LEFT, padx=2)
        ttk.Button(crop, text="Export cropped CSV",
                   command=self.export_crop).pack(side=tk.RIGHT)

        workspace = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        workspace.pack(fill=tk.BOTH, expand=True)
        sidebar = ttk.Frame(workspace, padding=(6, 2))
        plot_area = ttk.Frame(workspace)
        workspace.add(sidebar, weight=0)
        workspace.add(plot_area, weight=1)

        ttk.Label(sidebar, text="SIGNAL BROWSER").pack(anchor=tk.W)
        ttk.Label(sidebar, text="Ctrl-click to select multiple signals",
                  foreground="#5f6368").pack(anchor=tk.W, pady=(0, 3))
        signal_frame = ttk.Frame(sidebar)
        signal_frame.pack(fill=tk.BOTH, expand=True)
        self.signal_list = tk.Listbox(signal_frame, selectmode=tk.EXTENDED,
                                      exportselection=False, width=29, height=18)
        signal_scroll = ttk.Scrollbar(signal_frame, orient=tk.VERTICAL,
                                      command=self.signal_list.yview)
        self.signal_list.configure(yscrollcommand=signal_scroll.set)
        self.signal_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        signal_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        signal_controls = ttk.Frame(sidebar)
        signal_controls.pack(fill=tk.X, pady=(4, 7))
        ttk.Label(signal_controls, text="Axes:").pack(side=tk.LEFT)
        ttk.Spinbox(signal_controls, from_=1, to=8, width=4,
                    textvariable=self.axes_count_var).pack(side=tk.LEFT, padx=3)
        ttk.Button(signal_controls, text="Plot selected",
                   command=self.plot_selected).pack(side=tk.RIGHT)
        toggles = ttk.Frame(sidebar)
        toggles.pack(fill=tk.X, pady=(0, 6))
        ttk.Checkbutton(toggles, text="Grid", variable=self.grid_var,
                        command=self.redraw).pack(side=tk.LEFT)
        ttk.Checkbutton(toggles, text="Data cursor", variable=self.cursor_var,
                        command=self._cursor_mode_changed).pack(side=tk.LEFT, padx=6)
        ttk.Button(toggles, text="Clear cursors",
                   command=self.clear_cursors).pack(side=tk.RIGHT)

        ttk.Label(sidebar, text="INTERVAL STATISTICS").pack(anchor=tk.W)
        stats_frame = ttk.Frame(sidebar)
        stats_frame.pack(fill=tk.BOTH, expand=True)
        self.stats_table = ttk.Treeview(
            stats_frame, columns=("n", "min", "max", "mean", "std"),
            show="tree headings", height=11)
        self.stats_table.heading("#0", text="Signal")
        self.stats_table.column("#0", width=115, stretch=True)
        for name, width in (("n", 54), ("min", 68), ("max", 68),
                            ("mean", 68), ("std", 68)):
            self.stats_table.heading(name, text=name.upper())
            self.stats_table.column(name, width=width, anchor=tk.E, stretch=False)
        stats_x = ttk.Scrollbar(stats_frame, orient=tk.HORIZONTAL,
                                command=self.stats_table.xview)
        self.stats_table.configure(xscrollcommand=stats_x.set)
        self.stats_table.pack(fill=tk.BOTH, expand=True)
        stats_x.pack(fill=tk.X)
        ttk.Button(sidebar, text="Update statistics for crop",
                   command=self.update_statistics).pack(fill=tk.X, pady=(4, 0))

        self.figure = Figure(figsize=(12, 8), dpi=100, layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_area)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_plot_click)
        toolbar_frame = ttk.Frame(plot_area)
        toolbar_frame.pack(fill=tk.X)
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side=tk.LEFT, fill=tk.X)
        ttk.Label(toolbar_frame, text="Zoom/pan with toolbar; enable Select crop and drag on the bottom plot.",
                  padding=(12, 0)).pack(side=tk.LEFT)
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=4).pack(fill=tk.X, side=tk.BOTTOM)

    def open_dialog(self):
        selected = filedialog.askopenfilename(
            title="Open decoded flight CSV",
            initialdir=str(PROJECT_ROOT / "downloaded flight data"),
            filetypes=(("Flight CSV", "*.csv"), ("All files", "*.*")))
        if selected:
            self.open_path(Path(selected))

    def open_path(self, path):
        try:
            resolved = resolve_csv(str(path))
            self.status_var.set("Loading %s..." % resolved.name)
            self.root.update_idletasks()
            data, t0 = load_flight_csv(resolved)
        except Exception as exc:
            self.status_var.set("Load failed: %s" % exc)
            messagebox.showerror("Could not open flight", str(exc))
            return
        self.csv_path = resolved
        self.data = data
        self.t0_us = t0
        self.series_cache = {}
        valid_times = pd.to_numeric(data[INTERNAL_TIME], errors="coerce").dropna()
        self.full_range = (max(0.0, float(valid_times.min())), float(valid_times.max()))
        self.start_var.set("%.3f" % self.full_range[0])
        self.end_var.set("%.3f" % self.full_range[1])
        self.file_var.set("%s  |  %s rows  |  %.3f s" %
                          (resolved.name, f"{len(data):,}", self.full_range[1]))
        self.status_var.set("Loaded. Use toolbar zoom/pan or Select crop.")
        self._populate_signals()
        self.redraw()

    def _populate_signals(self):
        self.signal_list.delete(0, tk.END)
        excluded = {"t_us", "source", "marker", "message", INTERNAL_TIME}
        candidates = []
        for column in self.data.columns:
            if column in excluded:
                continue
            values = pd.to_numeric(self.data[column], errors="coerce")
            if values.notna().any():
                candidates.append(column)
        preferred = [column for column in SIGNAL_META if column in candidates]
        remaining = sorted(set(candidates).difference(preferred))
        for column in preferred + remaining:
            self.signal_list.insert(tk.END, column)
        wanted = set(DEFAULT_SIGNALS)
        for index in range(self.signal_list.size()):
            if self.signal_list.get(index) in wanted:
                self.signal_list.selection_set(index)

    def _selected_columns(self):
        return [self.signal_list.get(index) for index in self.signal_list.curselection()]

    def _select_columns(self, columns):
        wanted = set(columns)
        self.signal_list.selection_clear(0, tk.END)
        for index in range(self.signal_list.size()):
            if self.signal_list.get(index) in wanted:
                self.signal_list.selection_set(index)

    def _preset_changed(self, _event=None):
        name = self.preset_var.get()
        if name in PRESETS and self.data is not None:
            columns = [item[0] for panel in PRESETS[name] for item in panel[2]]
            self._select_columns(columns)
        self.redraw()

    def plot_selected(self):
        if not self._selected_columns():
            messagebox.showerror("No signals", "Select at least one signal in the browser.")
            return
        self.preset_var.set("Custom")
        self.redraw()

    def _series(self, column, source):
        key = (column, source)
        if key in self.series_cache:
            return self.series_cache[key]
        if self.data is None or column not in self.data.columns:
            result = (np.array([]), np.array([]))
        else:
            frame = self.data
            if source is not None:
                frame = frame[frame["source"] == source]
            x = pd.to_numeric(frame[INTERNAL_TIME], errors="coerce").to_numpy()
            y = pd.to_numeric(frame[column], errors="coerce").to_numpy()
            result = decimate_xy(x, y)
        self.series_cache[key] = result
        return result

    def redraw(self):
        if self.data is None:
            return
        old_xlim = self.axes[0].get_xlim() if self.axes else self.full_range
        self.figure.clear()
        preset_name = self.preset_var.get()
        if preset_name == "Custom":
            columns = self._selected_columns()
            if not columns:
                return
            axes_count = max(1, min(int(self.axes_count_var.get()), len(columns), 8))
            groups = [[] for _ in range(axes_count)]
            for index, column in enumerate(columns):
                meta = SIGNAL_META.get(column, (column, column, None, None))
                groups[index % axes_count].append((column, meta[0], meta[2], meta[3]))
            panels = [("Axes %d" % (index + 1), "Value", tuple(group))
                      for index, group in enumerate(groups)]
        else:
            panels = PRESETS[preset_name]
        self.axes = list(self.figure.subplots(len(panels), 1, sharex=True,
                                              squeeze=False)[:, 0])
        for axis, (title, ylabel, series_list) in zip(self.axes, panels):
            plotted = False
            for column, label, color, source in series_list:
                x, y = self._series(column, source)
                if len(x):
                    axis.plot(x, y, label=label, color=color, linewidth=0.85,
                              picker=4)
                    plotted = True
            axis.set_title(title, loc="left", fontsize=10)
            axis.set_ylabel(ylabel, fontsize=9)
            axis.grid(self.grid_var.get(), alpha=0.25)
            if plotted and len(series_list) > 1:
                axis.legend(loc="upper right", ncols=min(5, len(series_list)), fontsize=7)
            if not plotted:
                axis.text(0.5, 0.5, "No data for this panel", transform=axis.transAxes,
                          ha="center", va="center", color="#6b7280")
        self.axes[-1].set_xlabel("Time since log start (s)")
        self.axes[0].set_xlim(max(self.full_range[0], old_xlim[0]),
                              min(self.full_range[1], old_xlim[1]))
        self._draw_markers()
        self.selector = SpanSelector(
            self.axes[-1], self._on_span, "horizontal", useblit=True,
            props={"facecolor": "#2563eb", "alpha": 0.20}, interactive=True,
            drag_from_anywhere=True)
        self.selector.set_active(self.selection_enabled)
        self._shade_selection()
        self._draw_cursors()
        self.canvas.draw_idle()

    def _draw_markers(self):
        if "marker" not in self.data.columns:
            return
        marker_rows = self.data[self.data["source"] == "marker"]
        marker_rows = marker_rows.dropna(subset=[INTERNAL_TIME])
        for _, row in marker_rows.iterrows():
            x = float(row[INTERNAL_TIME])
            for axis in self.axes:
                axis.axvline(x, color="#6b7280", linewidth=0.6, alpha=0.35)

    def toggle_selection(self):
        self.selection_enabled = not self.selection_enabled
        self.select_button.configure(
            text="Select crop: ON" if self.selection_enabled else "Select crop: OFF")
        if self.selector is not None:
            self.selector.set_active(self.selection_enabled)
        self.status_var.set("Drag across the bottom plot to select a crop interval."
                            if self.selection_enabled else
                            "Crop selection disabled; toolbar zoom/pan is available.")

    def _on_span(self, start, end):
        start, end = self._clamp_range(start, end)
        self.start_var.set("%.6f" % start)
        self.end_var.set("%.6f" % end)
        self._shade_selection()
        self.status_var.set("Selected %.3f to %.3f s (%.3f s)." %
                            (start, end, end - start))
        self.update_statistics()

    def _cursor_mode_changed(self):
        if self.cursor_var.get() and self.selection_enabled:
            self.toggle_selection()
        self.status_var.set("Click a plot to place cursor A; click again for B."
                            if self.cursor_var.get() else
                            "Data cursor disabled.")

    def _on_plot_click(self, event):
        if not self.cursor_var.get() or event.inaxes not in self.axes or event.xdata is None:
            return
        self.cursor_times[self.cursor_slot] = float(event.xdata)
        self.cursor_slot = 1 - self.cursor_slot
        self._draw_cursors()
        values = []
        for line in event.inaxes.lines:
            if line.get_label().startswith("_"):
                continue
            x = np.asarray(line.get_xdata(), dtype=float)
            y = np.asarray(line.get_ydata(), dtype=float)
            valid = np.isfinite(x) & np.isfinite(y)
            if valid.any():
                idx = np.abs(x[valid] - event.xdata).argmin()
                values.append("%s=%g" % (line.get_label(), y[valid][idx]))
        slot = "B" if self.cursor_slot == 0 else "A"
        delta = ""
        if all(value is not None for value in self.cursor_times):
            delta = " | dT=%.6f s" % abs(self.cursor_times[1] - self.cursor_times[0])
        self.status_var.set("Cursor %s: %.6f s | %s%s" %
                            (slot, event.xdata, ", ".join(values), delta))

    def _draw_cursors(self):
        for artist in self.cursor_lines + self.cursor_annotations:
            try:
                artist.remove()
            except ValueError:
                pass
        self.cursor_lines = []
        self.cursor_annotations = []
        colors = ("#d62728", "#9467bd")
        for slot, value in enumerate(self.cursor_times):
            if value is None:
                continue
            for axis in self.axes:
                self.cursor_lines.append(axis.axvline(value, color=colors[slot],
                                                       linewidth=1.0, alpha=0.85))
            if self.axes:
                self.cursor_annotations.append(self.axes[0].annotate(
                    "%s %.6fs" % ("AB"[slot], value), (value, 1.0),
                    xycoords=("data", "axes fraction"), xytext=(3, -3),
                    textcoords="offset points", va="top", color=colors[slot],
                    fontsize=8))
        self.canvas.draw_idle()

    def clear_cursors(self):
        self.cursor_times = [None, None]
        self.cursor_slot = 0
        self._draw_cursors()

    def update_statistics(self):
        if self.data is None:
            return
        try:
            start, end = self._entry_range()
        except Exception:
            return
        columns = self._selected_columns()
        if not columns and self.preset_var.get() in PRESETS:
            columns = [item[0] for panel in PRESETS[self.preset_var.get()]
                       for item in panel[2]]
        for item in self.stats_table.get_children():
            self.stats_table.delete(item)
        for column, count, minimum, maximum, mean, std in interval_statistics(
                self.data, columns, start, end):
            self.stats_table.insert("", tk.END, text=column,
                                    values=(f"{count:,}", f"{minimum:.6g}",
                                            f"{maximum:.6g}", f"{mean:.6g}",
                                            f"{std:.6g}"))

    def _clamp_range(self, start, end):
        start, end = sorted((float(start), float(end)))
        start = max(self.full_range[0], start)
        end = min(self.full_range[1], end)
        if end <= start:
            raise ValueError("crop end must be greater than crop start")
        return start, end

    def _entry_range(self):
        try:
            return self._clamp_range(float(self.start_var.get()),
                                     float(self.end_var.get()))
        except Exception as exc:
            messagebox.showerror("Invalid crop interval", str(exc))
            raise

    def _shade_selection(self):
        for patch in self.selection_patches:
            try:
                patch.remove()
            except ValueError:
                pass
        self.selection_patches = []
        if not self.axes:
            return
        try:
            start, end = self._clamp_range(float(self.start_var.get()),
                                           float(self.end_var.get()))
        except Exception:
            return
        self.selection_patches = [axis.axvspan(start, end, color="#2563eb",
                                               alpha=0.08, zorder=-1)
                                  for axis in self.axes]
        self.canvas.draw_idle()

    def use_visible_window(self):
        if not self.axes:
            return
        start, end = self._clamp_range(*self.axes[0].get_xlim())
        self.start_var.set("%.6f" % start)
        self.end_var.set("%.6f" % end)
        self._shade_selection()
        self.status_var.set("Crop boundaries copied from the visible window.")

    def zoom_to_crop(self):
        if not self.axes:
            return
        try:
            start, end = self._entry_range()
        except Exception:
            return
        self.axes[0].set_xlim(start, end)
        self.canvas.draw_idle()

    def full_view(self):
        if self.axes:
            self.axes[0].set_xlim(*self.full_range)
            self.canvas.draw_idle()

    def export_crop(self):
        if self.data is None:
            messagebox.showerror("No flight", "Open a flight CSV first.")
            return
        try:
            start, end = self._entry_range()
        except Exception:
            return
        suggested = "%s_crop_%0.3f-%0.3fs.csv" % (self.csv_path.stem, start, end)
        output = filedialog.asksaveasfilename(
            title="Export cropped mission CSV", initialdir=str(self.csv_path.parent),
            initialfile=suggested, defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),))
        if not output:
            return
        cropped = crop_frame(self.data, start, end)
        if cropped.empty:
            messagebox.showerror("Empty interval", "No mission records fall inside this interval.")
            return
        try:
            cropped.to_csv(output, index=False)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        size = Path(output).stat().st_size
        text = ("Exported %s rows covering %.3f seconds\n%s\n%.2f MiB" %
                (f"{len(cropped):,}", end - start, output, size / 1048576.0))
        self.status_var.set(text.replace("\n", "  |  "))
        messagebox.showinfo("Cropped flight exported", text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flight", nargs="?", help="flight name or decoded CSV path")
    args = parser.parse_args(argv)
    root = tk.Tk()
    app = FlightInspector(root)
    if args.flight:
        root.after(50, lambda: app.open_path(args.flight))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
