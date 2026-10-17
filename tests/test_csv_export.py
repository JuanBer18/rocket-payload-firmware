import os
import struct
import tempfile
import unittest

import recfmt
from core.csv_export import export
from core.logger import BinaryLogger


class Clock:
    @staticmethod
    def us():
        return 0


HEADER = {
    "imu": {"accel_mg_per_lsb": 0.976, "gyro_mdps_per_lsb": 70.0,
            "temp_lsb_per_c": 256.0, "temp_offset_c": 25.0,
            "timestamp_res_us": 25.0},
    "baro": {"has_humidity": False, "calibration": {
        "T1": 27504, "T2": 26435, "T3": -1000,
        "P1": 36477, "P2": -10685, "P3": 3024, "P4": 2855,
        "P5": 140, "P6": -7, "P7": 15500, "P8": -14600,
        "P9": 6000}},
    "sen65": {},
}


class CsvExportTests(unittest.TestCase):
    def test_streaming_export_keeps_binary_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = os.path.join(temp, "flight_001.bin")
            csv_path = os.path.join(temp, "flight_001.csv")
            logger = BinaryLogger(binary, Clock(), flush_every=100).open(HEADER)

            timestamp = bytes([(4 << 3)]) + struct.pack("<I", 100) + b"\0\0"
            accel = bytes([(2 << 3)]) + struct.pack("<3h", 1000, 0, -1000)
            gyro = bytes([(1 << 3)]) + struct.pack("<3h", 10, 20, 30)
            fifo = timestamp + accel + gyro
            logger.write_imu_fifo(4000, fifo, 3)
            logger.write_baro(5000, 415148, 519888, 0)
            logger.write_sen65(6000, (10, 20, 30, 40, 4500, 4200, 1000, 1000))
            logger.write_sen65_raw(6000, (4500, 4200, 12345, 23456))
            logger.write_sen65_number(6000, (100, 90, 50, 20, 10))
            logger.write_marker(7000, recfmt.MARK_TEST, "fixture")
            logger.write_timesync(1000, 0)
            logger.write_timesync(3500, 100)
            logger.close()

            result = export(binary, csv_path)
            self.assertTrue(os.path.exists(binary))
            self.assertTrue(os.path.exists(csv_path))
            self.assertFalse(result["binary_deleted"])
            self.assertGreater(result["rows"], 7)
            self.assertTrue(result["timestamp_fit"])
            with open(csv_path, encoding="utf-8") as source:
                text = source.read()
            self.assertIn("source", text)
            self.assertIn("sen65_raw", text)
            self.assertIn("sen65_number", text)
            self.assertIn("barometer", text)
            self.assertIn("fixture", text)

    def test_bad_binary_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = os.path.join(temp, "bad.bin")
            with open(binary, "wb") as target:
                target.write(b"not a flight log")
            with self.assertRaises(Exception):
                export(binary, os.path.join(temp, "bad.csv"), delete_binary=True)
            self.assertTrue(os.path.exists(binary))


if __name__ == "__main__":
    unittest.main()
