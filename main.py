"""Automatic payload entry point. MicroPython runs this after every boot.

Wi-Fi starts before the SD card and sensors. This makes cold-start failures
visible in the dashboard and lets the controller retry instead of silently
ending main.py and appearing dead until somebody presses Reset.
"""

import gc
import time

import config
from core import buses, storage
from core import ota
from dashboard import Dashboard
from mission import MissionController


class BootStatus:
    """Dashboard-compatible status while payload hardware is retrying."""

    def __init__(self):
        self.state = "BOOTING"
        self.message = "waiting for payload hardware"
        self.attempt = 0
        self.last_error = None
        self.logger = None
        self.mode = MissionController._load_boot_mode()
        self._boot_ms = time.ticks_ms()

    def status_snapshot(self):
        empty = {}
        return {
            "state": self.state,
            "message": self.message,
            "mission": {"mode": "not ready", "next_boot_mode": self.mode,
                        "auto_stop": False, "elapsed_s": 0,
                        "liftoff_threshold_g": config.LIFTOFF_ACCEL_G,
                        "liftoff_dwell_ms": config.LIFTOFF_DWELL_MS},
            "altitude": empty, "imu": empty, "bme280": empty, "sen65": empty,
            "system": {"boot_attempt": self.attempt, "errors": self.attempt,
                       "uptime_s": time.ticks_diff(time.ticks_ms(), self._boot_ms) / 1000.0,
                       "log_path": None, "log_bytes": None,
                       "fifo_overruns": 0, "sd_free_bytes": None,
                       "heap_free_bytes": None, "logging_active": False,
                       "logging_errors": 0, "last_test": None,
                       "last_error": self.last_error,
                       "note": "Wi-Fi is healthy; sensor/SD startup is retrying"},
            "sensor_health": {},
        }

    def list_logs(self):
        return []

    def _not_ready(self, *args):
        return {"ok": False, "error": "payload hardware is still starting",
                "last_error": self.last_error}

    start_mission = _not_ready
    stop_mission = _not_ready
    set_boot_mode = _not_ready
    return_to_idle = _not_ready
    start_sen65 = _not_ready
    stop_sen65 = _not_ready
    zero_altitude = _not_ready
    calibrate_gyro = _not_ready
    imu_self_test = _not_ready
    clean_sen65 = _not_ready


def _start_dashboard(status):
    last_error = None
    for attempt in range(1, config.DASHBOARD_START_RETRIES + 1):
        candidate = Dashboard(status)
        try:
            candidate.start()
            return candidate
        except Exception as exc:
            last_error = exc
            print("dashboard start attempt %d failed: %r" % (attempt, exc))
            try:
                if candidate.sock:
                    candidate.sock.close()
                if candidate.ap:
                    candidate.ap.active(False)
            except Exception:
                pass
            time.sleep_ms(500 * attempt)
    print("dashboard unavailable after retries: %r" % (last_error,))
    return None


def _cleanup_failed_boot(candidate):
    try:
        candidate._close_log("boot attempt failed")
    except Exception:
        pass
    try:
        if candidate.sen and candidate.sen.measuring:
            candidate.sen.stop_measurement()
    except Exception:
        pass
    try:
        if candidate.imu:
            candidate.imu.fifo_bypass()
    except Exception:
        pass
    storage.unmount()
    gc.collect()


def main():
    global payload, web
    print("\nRocket payload nominal mission firmware starting")
    time.sleep_ms(config.POWER_SETTLE_MS)
    boot_status = BootStatus()
    web = _start_dashboard(boot_status)
    if web:
        ota.confirm_boot()
    payload = None

    while payload is None:
        boot_status.attempt += 1
        boot_status.message = "initialising sensors and SD (attempt %d)" % boot_status.attempt
        candidate = MissionController()
        try:
            # Release an I2C slave left mid-byte by an interrupted power cycle.
            buses.bus_recovery(config.IMU_BUS)
            if config.SEN65_BUS != config.IMU_BUS:
                buses.bus_recovery(config.SEN65_BUS)
            payload = candidate.boot()
        except Exception as exc:
            boot_status.last_error = repr(exc)
            boot_status.message = "payload startup failed; retrying"
            print("payload boot attempt %d failed: %r" %
                  (boot_status.attempt, exc))
            _cleanup_failed_boot(candidate)
            deadline = time.ticks_add(time.ticks_ms(), config.BOOT_RETRY_DELAY_MS)
            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                if web:
                    web.poll()
                time.sleep_ms(10)

    if web:
        web.mission = payload
    else:
        web = _start_dashboard(payload)
    payload.run_forever(web)


if __name__ == "__main__":
    main()
