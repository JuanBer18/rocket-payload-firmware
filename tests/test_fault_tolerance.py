import sys
import time
import types
import unittest


machine = types.ModuleType("machine")
machine.Pin = object
machine.SDCard = object
machine.SoftI2C = object
machine.I2C = object
sys.modules.setdefault("machine", machine)

if not hasattr(time, "ticks_us"):
    time.ticks_us = lambda: int(time.monotonic() * 1_000_000)
    time.ticks_ms = lambda: int(time.monotonic() * 1_000)
    time.ticks_diff = lambda a, b: a - b
    time.ticks_add = lambda a, b: a + b
    time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)

import config
import mission


class FakeLogger:
    def __init__(self, fail_write=False, fail_close=False):
        self.path = "/sd/test.bin"
        self.fail_write = fail_write
        self.fail_close = fail_close
        self.closed = False
        self.markers = []

    def write_marker(self, _t, tag, text):
        if self.fail_write:
            raise OSError("simulated SD write failure")
        self.markers.append((tag, text))

    def write_baro(self, *_args):
        if self.fail_write:
            raise OSError("simulated SD write failure")

    def flush(self):
        if self.fail_close:
            raise OSError("simulated flush failure")

    def close(self):
        self.closed = True
        if self.fail_close:
            raise OSError("simulated close failure")


class FailingBarometer:
    def read_raw(self):
        raise OSError("simulated BME280 disconnect")


class FailingStopImu:
    def power_down_accel(self):
        raise OSError("simulated IMU stop error")


class FailingStopSen:
    measuring = True

    def stop_measurement(self):
        raise OSError("simulated SEN65 stop error")


class FaultToleranceTests(unittest.TestCase):
    def test_repeated_baro_failure_does_not_close_logger(self):
        payload = mission.MissionController(log=lambda _text: None)
        payload.state = mission.MISSION_ACTIVE
        payload.logger = FakeLogger()
        payload.baro = FailingBarometer()
        for _ in range(config.SENSOR_ERROR_REINIT_COUNT):
            payload._sample_baro(force=True)
        self.assertIsNone(payload.baro)
        self.assertIsNotNone(payload.logger)
        self.assertEqual(payload.sensor_health["bme280"]["errors"],
                         config.SENSOR_ERROR_REINIT_COUNT)

    def test_sd_write_failure_does_not_remove_sensors(self):
        payload = mission.MissionController(log=lambda _text: None)
        payload.imu = object()
        payload.baro = object()
        payload.sen = object()
        payload.logger = FakeLogger(fail_write=True)
        self.assertFalse(payload._log_write("write_baro", 1, 2, 3, 4))
        self.assertIsNone(payload.logger)
        self.assertIsNotNone(payload.imu)
        self.assertIsNotNone(payload.baro)
        self.assertIsNotNone(payload.sen)
        self.assertEqual(payload.logging_errors, 1)

    def test_sensor_stop_errors_cannot_block_safe_bin_close(self):
        payload = mission.MissionController(log=lambda _text: None)
        payload.state = mission.MISSION_ACTIVE
        payload.imu = FailingStopImu()
        payload.sen = FailingStopSen()
        logger = FakeLogger()
        payload.logger = logger
        safe = payload._stop_and_close("fault injection")
        self.assertTrue(safe)
        self.assertTrue(logger.closed)
        self.assertTrue(payload.safe_to_power_off)
        self.assertEqual(payload.state, mission.MISSION_COMPLETE)
        self.assertIn("SAFE TO POWER OFF", payload.state_message)

    def test_failed_flush_never_claims_safe_to_power_off(self):
        payload = mission.MissionController(log=lambda _text: None)
        payload.state = mission.MISSION_ACTIVE
        payload.logger = FakeLogger(fail_close=True)
        safe = payload._stop_and_close("fault injection")
        self.assertFalse(safe)
        self.assertFalse(payload.safe_to_power_off)
        self.assertIn("not verified", payload.state_message)


if __name__ == "__main__":
    unittest.main()
