import os
import tempfile
import unittest

import recfmt
from core.logger import BinaryLogger
from tools.import_sd_logs import import_logs


class Clock:
    @staticmethod
    def us():
        return 1234


HEADER = {
    "imu": {"accel_mg_per_lsb": 0.976, "gyro_mdps_per_lsb": 70.0,
            "timestamp_res_us": 25.0},
    "baro": {"has_humidity": False, "calibration": {}},
    "sen65": {},
}


class ImportSdLogsTests(unittest.TestCase):
    def test_copy_decode_and_preserve_source(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, "card")
            output = os.path.join(temp, "import")
            os.mkdir(source)
            binary = os.path.join(source, "flight_001.bin")
            logger = BinaryLogger(binary, Clock()).open(HEADER)
            logger.write_marker(1234, recfmt.MARK_TEST, "import fixture")
            logger.close()
            with open(binary, "rb") as source_file:
                original = source_file.read()

            result = import_logs(source, output, make_plots=False)

            self.assertEqual(len(result["copied"]), 1)
            self.assertEqual(len(result["converted"]), 1)
            self.assertEqual(result["failures"], [])
            self.assertTrue(os.path.exists(result["manifest"]))
            with open(binary, "rb") as source_file:
                self.assertEqual(source_file.read(), original)
            _src, csv_path, report_path, rows = result["converted"][0]
            self.assertGreater(rows, 0)
            self.assertTrue(os.path.exists(csv_path))
            self.assertTrue(os.path.exists(report_path))

    def test_rejects_card_without_payload_logs(self):
        with tempfile.TemporaryDirectory() as source:
            with self.assertRaisesRegex(ValueError, "no flight"):
                import_logs(source, os.path.join(source, "output"),
                            make_plots=False)


if __name__ == "__main__":
    unittest.main()
