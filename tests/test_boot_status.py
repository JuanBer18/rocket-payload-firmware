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

from main import BootStatus


class BootStatusTests(unittest.TestCase):
    def test_dashboard_status_exists_before_hardware_boot(self):
        status = BootStatus()
        snapshot = status.status_snapshot()
        self.assertEqual(snapshot["state"], "BOOTING")
        self.assertIn("sensor/SD startup is retrying",
                      snapshot["system"]["note"])
        self.assertFalse(status.stop_mission()["ok"])


if __name__ == "__main__":
    unittest.main()
