from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from tools.inspect_flight import (INTERNAL_TIME, crop_frame, decimate_xy,
                                  interval_statistics, load_flight_csv, resolve_csv)
from tools.analyze_flight import default_output_for


class InspectFlightTests(unittest.TestCase):
    def test_load_and_crop_preserve_original_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flight_test.csv"
            pd.DataFrame({
                "t_us": [1_000_000, 2_000_000, 3_000_000, 4_000_000],
                "source": ["imu", "baro", "sen65", "marker"],
                "a_mag_g": [1.0, np.nan, np.nan, np.nan],
                "pressure_pa": [np.nan, 101325.0, np.nan, np.nan],
            }).to_csv(path, index=False)
            data, t0 = load_flight_csv(path)
            self.assertEqual(t0, 1_000_000)
            self.assertIn(INTERNAL_TIME, data)
            cropped = crop_frame(data, 0.9, 2.1)
            self.assertEqual(cropped["t_us"].tolist(), [2_000_000, 3_000_000])
            self.assertNotIn(INTERNAL_TIME, cropped)
            self.assertNotIn("pressure_hpa", cropped)

    def test_decimation_preserves_short_peak(self):
        x = np.arange(100_000, dtype=float)
        y = np.zeros_like(x)
        y[54321] = 99.0
        dx, dy = decimate_xy(x, y, max_points=1000)
        self.assertLessEqual(len(dx), 1100)
        self.assertEqual(float(dy.max()), 99.0)

    def test_resolve_rejects_missing_flight_with_helpful_message(self):
        with self.assertRaisesRegex(FileNotFoundError, "process_flight.py"):
            resolve_csv("flight_that_does_not_exist")

    def test_interval_statistics_ignore_other_times_and_nan(self):
        data = pd.DataFrame({
            INTERNAL_TIME: [0.0, 1.0, 2.0, 3.0],
            "signal": [100.0, 2.0, np.nan, 4.0],
        })
        stats = interval_statistics(data, ["signal"], 0.5, 3.0)
        self.assertEqual(stats[0][0:2], ("signal", 2))
        self.assertEqual(stats[0][2], 2.0)
        self.assertEqual(stats[0][3], 4.0)
        self.assertEqual(stats[0][4], 3.0)

    def test_analyzer_reuses_existing_analysis_folder(self):
        csv = Path("somewhere") / "flight_003_analysis" / "flight_003.csv"
        self.assertEqual(default_output_for(csv), csv.parent)

    def test_analyzer_creates_analysis_folder_beside_plain_csv(self):
        csv = Path("somewhere") / "flight_003.csv"
        self.assertEqual(default_output_for(csv),
                         csv.parent / "flight_003_analysis")


if __name__ == "__main__":
    unittest.main()
