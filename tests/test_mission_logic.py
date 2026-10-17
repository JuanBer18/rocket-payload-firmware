import math
import sys
import time
import types
import unittest


# Permit importing the firmware modules on desktop CPython. Hardware objects
# are never constructed in these logic tests.
machine = types.ModuleType("machine")
machine.Pin = object
machine.SDCard = object
machine.SoftI2C = object
machine.I2C = object
sys.modules.setdefault("machine", machine)

# MicroPython's wrap-safe ticks API is absent on CPython. These shims are
# sufficient for importing the shared monotonic clock and exercising logic.
if not hasattr(time, "ticks_us"):
    time.ticks_us = lambda: int(time.monotonic() * 1_000_000)
    time.ticks_ms = lambda: int(time.monotonic() * 1_000)
    time.ticks_diff = lambda a, b: a - b
    time.ticks_add = lambda a, b: a + b
    time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)

from core.altitude import BarometricAltitude
import mission
import dashboard


class FakeTicks:
    @staticmethod
    def ticks_diff(a, b):
        return a - b


class AltitudeTests(unittest.TestCase):
    def test_zero_is_current_pressure(self):
        estimator = BarometricAltitude(tau_s=0.5)
        altitude, speed = estimator.update(101325.0, 0)
        self.assertAlmostEqual(altitude, 0.0)
        self.assertAlmostEqual(speed, 0.0)
        self.assertEqual(estimator.reference_pa, 101325.0)

    def test_pressure_drop_produces_positive_height_and_speed(self):
        estimator = BarometricAltitude(tau_s=0.5)
        estimator.update(101325.0, 0)
        altitude, speed = estimator.update(100129.0, 1_000_000)
        self.assertGreater(altitude, 95.0)
        self.assertLess(altitude, 105.0)
        self.assertGreater(speed, 0.0)

    def test_rezero_clears_height_and_speed(self):
        estimator = BarometricAltitude()
        estimator.update(101325.0, 0)
        estimator.update(100129.0, 1_000_000)
        self.assertTrue(estimator.zero())
        self.assertEqual(estimator.altitude_m, 0.0)
        self.assertEqual(estimator.vertical_speed_mps, 0.0)


class LiftoffTests(unittest.TestCase):
    def setUp(self):
        self.real_time = mission.time
        mission.time = FakeTicks()

    def tearDown(self):
        mission.time = self.real_time

    def test_requires_continuous_dwell(self):
        detector = mission.LiftoffDetector(3.0, 50)
        self.assertFalse(detector.update(3.1, 100))
        self.assertFalse(detector.update(3.2, 149))
        self.assertTrue(detector.update(3.2, 150))

    def test_dip_resets_dwell(self):
        detector = mission.LiftoffDetector(3.0, 50)
        detector.update(3.1, 100)
        self.assertFalse(detector.update(2.9, 140))
        self.assertFalse(detector.update(3.1, 160))
        self.assertFalse(detector.update(3.1, 200))
        self.assertTrue(detector.update(3.1, 210))


class FakeMission:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.state = mission.MISSION_COMPLETE
        self.logger = None
        self.maintenance = False

    def start_mission(self, source):
        self.started = source
        return {"ok": True}

    def stop_mission(self):
        self.stopped = True
        return {"ok": True}

    def set_maintenance_sampling(self, enabled):
        self.maintenance = bool(enabled)
        return {"ok": True, "maintenance_sampling": self.maintenance}


class DashboardTests(unittest.TestCase):
    def test_simulation_and_stop_actions(self):
        payload = FakeMission()
        web = dashboard.Dashboard(payload)
        result = web._action("/api/action?action=start")
        self.assertTrue(result["ok"])
        self.assertEqual(payload.started, "dashboard start")
        result = web._action("/api/action?action=stop")
        self.assertTrue(result["ok"])
        self.assertTrue(payload.stopped)

    def test_page_contains_requested_controls(self):
        for label in ("STATUS", "MISSION CONTROLS", "DEBUG / FILES",
                      "START NEW MISSION", "STOP + CLOSE SAFELY",
                      "TIME SINCE POWER-UP", "CURRENT BIN", "SEN65",
                      "IMU", "BAROMETER", "VOC INDEX", "NOX INDEX",
                      "PM1.0", "PM2.5", "PM4.0", "PM10",
                      "ZERO ALTITUDE HERE", "CALIBRATE GYRO",
                      "RUN SELF-TEST", "TURN LIVE PLOTS ON",
                      "WIRELESS SOURCE UPDATE", "STAGE + CHECK FILE"):
            self.assertIn(label, dashboard.PAGE)

    def test_live_plots_are_essential_only_and_pm_is_combined(self):
        self.assertIn("title:'Altitude AGL'", dashboard.PAGE)
        self.assertIn("title:'Acceleration magnitude'", dashboard.PAGE)
        self.assertIn("title:'Pressure'", dashboard.PAGE)
        self.assertIn("title:'VOC index'", dashboard.PAGE)
        self.assertIn("title:'Particulate matter'", dashboard.PAGE)
        self.assertIn("['sen65','pm1p0','PM1.0'", dashboard.PAGE)
        self.assertIn("['sen65','pm10','PM10'", dashboard.PAGE)
        self.assertNotIn("title:'SEN temperature'", dashboard.PAGE)

    def test_graphs_are_off_by_default_and_toggle_in_ram(self):
        web = dashboard.Dashboard(FakeMission())
        self.assertFalse(web._graph_status()["graphs_enabled"])
        result = web._action("/api/action?action=graphs_on")
        self.assertTrue(result["graphs_enabled"])
        self.assertTrue(web.mission.maintenance)
        self.assertGreater(result["graphs_remaining_s"], 0)
        result = web._action("/api/action?action=graphs_off")
        self.assertFalse(result["graphs_enabled"])
        self.assertFalse(web.mission.maintenance)

    def test_graphs_expire(self):
        web = dashboard.Dashboard(FakeMission())
        web._set_graphs(True)
        web._graphs_deadline_ms = time.ticks_add(time.ticks_ms(), -1)
        self.assertFalse(web._graph_status()["graphs_enabled"])

    def test_recording_locks_debug_and_idle_controls(self):
        payload = FakeMission()
        payload.state = mission.MISSION_ACTIVE
        payload.logger = object()
        web = dashboard.Dashboard(payload)
        self.assertFalse(web._action("/api/action?action=start")["ok"])
        self.assertFalse(web._action("/api/action?action=debug_on")["ok"])
        self.assertFalse(web._action("/api/action?action=graphs_on")["ok"])

    def test_debug_mode_is_ram_only_and_requires_no_open_log(self):
        payload = FakeMission()
        web = dashboard.Dashboard(payload)
        self.assertTrue(web._action("/api/action?action=debug_on")["ok"])
        self.assertTrue(web.debug_enabled)
        self.assertTrue(web._action("/api/action?action=debug_off")["ok"])
        self.assertFalse(web.debug_enabled)

    def test_url_decodes_update_destination(self):
        self.assertEqual(dashboard.Dashboard._param(
            "/api/update?name=drivers%2Fsen65.py", "name"),
            "drivers/sen65.py")


if __name__ == "__main__":
    unittest.main()
