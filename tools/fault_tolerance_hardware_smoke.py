"""Focused on-board smoke test for fault isolation and safe shutdown."""

import time

from core import storage
from mission import MissionController, MISSION_COMPLETE


class FailingBarometer:
    """A controlled read failure used to prove other logging survives."""

    def read_raw(self):
        raise OSError("intentional BME280 smoke-test fault")


payload = MissionController().boot()
first_path = payload.logger.path
print("boot", payload.state, first_path)
print("initial_health", payload.sensor_health)

# Let the real sensors and logger run together before injecting one failure.
deadline = time.ticks_add(time.ticks_ms(), 3000)
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    payload.tick()
    time.sleep_ms(5)

if payload.logger is None:
    raise AssertionError("logging stopped before the injected fault")

# Five failed BME reads should quarantine only that sensor. The open log and
# the other sensor objects must remain alive.
payload.baro = FailingBarometer()
for _ in range(5):
    payload._sample_baro(force=True)

if payload.baro is not None:
    raise AssertionError("failed BME280 was not quarantined")
if payload.logger is None:
    raise AssertionError("BME280 fault stopped logging")
if payload.imu is None or payload.sen is None:
    raise AssertionError("BME280 fault disabled an unrelated sensor")
print("isolated_fault", payload.sensor_health["bme280"])

# Recovery must reinitialise the physical BME280 and rotate to a continuation
# BIN whose header includes its calibration data.
payload._recover_sensors(force=True)
if payload.baro is None or not payload.sensor_health["bme280"]["available"]:
    raise AssertionError("BME280 did not recover")
if payload.logger is None:
    raise AssertionError("logging did not survive sensor recovery")
second_path = payload.logger.path
if second_path == first_path:
    raise AssertionError("sensor recovery did not rotate the BIN")
print("recovered", second_path, payload.sensor_health["bme280"])

deadline = time.ticks_add(time.ticks_ms(), 1500)
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    payload.tick()
    time.sleep_ms(5)

result = payload.stop_mission()
print("stop", result)
if payload.state != MISSION_COMPLETE:
    raise AssertionError("safe stop did not reach MISSION_COMPLETE")
if payload.logger is not None:
    raise AssertionError("safe stop left a BIN handle open")
if not payload.safe_to_power_off or not payload.log_close_verified:
    raise AssertionError("safe shutdown was not verified")

storage.unmount()
print("SMOKE_PASS", first_path, second_path)
