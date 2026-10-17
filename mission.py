"""Nominal payload mission controller for the fully assembled ESP32-S3."""

import gc
import json
import math
import os
import time

import config
import recfmt
from core import buses, clock, storage
from core.altitude import BarometricAltitude
from core.logger import BinaryLogger, build_header, next_log_path
from drivers.bme280 import BME280, compensate_h, compensate_p, compensate_t
from drivers.lsm6dso32 import LSM6DSO32, WORD_LEN
from drivers.sen65 import CHANNELS, SEN65


PAD_IDLE = "PAD_IDLE"
MISSION_ACTIVE = "MISSION_ACTIVE"
MISSION_COMPLETE = "MISSION_COMPLETE"


def _round(value, places=2):
    return None if value is None else round(value, places)


class LiftoffDetector:
    def __init__(self, threshold_g, dwell_ms):
        self.threshold_g = float(threshold_g)
        self.dwell_ms = int(dwell_ms)
        self._above_since = None

    def reset(self):
        self._above_since = None

    def update(self, accel_magnitude_g, now_ms):
        if accel_magnitude_g < self.threshold_g:
            self._above_since = None
            return False
        if self._above_since is None:
            self._above_since = now_ms
            return self.dwell_ms == 0
        return time.ticks_diff(now_ms, self._above_since) >= self.dwell_ms


class MissionController:
    """Owns sensors, logging, state transitions, and dashboard commands."""

    def __init__(self, log=print):
        self.log = log
        self.state = "BOOTING"
        self.state_message = "initialising"
        self.mode = self._load_boot_mode()
        self.next_boot_mode = self.mode
        self.launch_us = None
        self.complete_us = None
        self.i2c0 = None
        self.i2c1 = None
        self.imu = None
        self.baro = None
        self.sen = None
        self.logger = None
        self.altitude = BarometricAltitude(config.VERTICAL_SPEED_TAU_S)
        self.liftoff = LiftoffDetector(config.LIFTOFF_ACCEL_G,
                                      config.LIFTOFF_DWELL_MS)
        self.gyro_bias_dps = None
        self.last_test = "none"
        self.last_error = None
        self.errors = 0
        self.fifo_overruns = 0
        self.safe_to_power_off = False
        self.log_close_verified = False
        self.logging_errors = 0
        self.maintenance_sampling = False
        self.sensor_health = {
            "imu": self._new_health(),
            "bme280": self._new_health(),
            "sen65": self._new_health(),
        }
        self._fifo_buf = bytearray(config.IMU_FIFO_WATERMARK * WORD_LEN)
        self._last_imu = None
        self._last_bme = None
        self._last_sen = None
        self._sen_status = 0
        self._last_imu_ms = -1000
        self._last_baro_ms = -1000
        self._last_sen_ms = -1000
        self._last_sen_status_ms = -10000
        self._last_sync_ms = -1000
        self._last_web_sample_ms = -1000
        self._last_sensor_recovery_ms = -10000
        self._last_logger_recovery_ms = -10000
        self._boot_us = clock.us()

    @staticmethod
    def _new_health():
        return {"available": False, "errors": 0, "consecutive_errors": 0,
                "last_error": None, "last_ok_us": None}

    # ------------------------------------------------------------- lifecycle
    @staticmethod
    def _load_boot_mode():
        try:
            with open(config.MISSION_MODE_FILE, "r") as source:
                mode = json.load(source).get("mode")
            if mode in (config.MISSION_MODE_LAUNCH,
                        config.MISSION_MODE_CONTINUOUS):
                return mode
        except Exception:
            pass
        return config.MISSION_MODE_DEFAULT

    def boot(self):
        buses.check_collision(True, True)
        self._ensure_buses()
        self._recover_sensors(force=True)
        # SD is the one required component: without storage there is nowhere
        # to preserve data. main.py catches this and retries while Wi-Fi stays
        # available. Individual sensor failures never abort boot.
        storage.mount()
        if self.mode == config.MISSION_MODE_CONTINUOUS:
            self._enter_continuous("power on")
        else:
            self._enter_idle("power on")
        return self

    def _ensure_buses(self):
        if self.i2c0 is None:
            try:
                self.i2c0 = buses.init_bus(config.IMU_BUS)
            except Exception as exc:
                self._record_error("I2C0", exc)
        if config.SEN65_BUS == config.IMU_BUS:
            self.i2c1 = self.i2c0
        elif self.i2c1 is None:
            try:
                self.i2c1 = buses.init_bus(config.SEN65_BUS)
            except Exception as exc:
                self._record_error("I2C1", exc)

    def _recover_sensors(self, force=False):
        now_ms = time.ticks_ms()
        if (not force and time.ticks_diff(now_ms, self._last_sensor_recovery_ms) <
                config.SENSOR_RECOVERY_INTERVAL_MS):
            return
        self._last_sensor_recovery_ms = now_ms
        self._ensure_buses()
        recovered_names = []

        if self.imu is None and self.i2c0 is not None:
            try:
                sensor = LSM6DSO32(
                    self.i2c0, config.IMU_ADDR, config.IMU_ACCEL_FS_G,
                    config.IMU_GYRO_FS_DPS, config.PAD_IMU_ODR_HZ).init()
                self.imu = sensor
                target_rate = (config.IMU_ODR_HZ if self.state == MISSION_ACTIVE
                               else config.PAD_IMU_ODR_HZ)
                if not self._configure_imu(target_rate):
                    raise OSError("IMU configuration failed")
                self._sensor_ok("imu", recovered=True)
                recovered_names.append("imu")
            except Exception as exc:
                self.imu = None
                self._record_error("IMU recovery", exc)

        if self.baro is None and self.i2c0 is not None:
            try:
                self.baro = BME280(self.i2c0, config.BARO_ADDR).init()
                self._sensor_ok("bme280", recovered=True)
                recovered_names.append("bme280")
            except Exception as exc:
                self.baro = None
                self._record_error("BME280 recovery", exc)

        if self.sen is None and self.i2c1 is not None:
            try:
                self.sen = SEN65(self.i2c1, config.SEN65_ADDR).init()
                if (self.state == MISSION_ACTIVE or
                        self.mode == config.MISSION_MODE_CONTINUOUS):
                    self.sen.start_measurement()
                self._sensor_ok("sen65", recovered=True)
                recovered_names.append("sen65")
            except Exception as exc:
                self.sen = None
                self._record_error("SEN65 recovery", exc)

        # A newly recovered sensor may require calibration/scale coefficients
        # that were absent from the current file header. Rotate to a new BIN
        # immediately so every subsequent raw record is independently
        # decodable. Other sensors continue across the short rotation.
        if recovered_names and self.logger and self.state != "BOOTING":
            self._drain_fifo(all_available=True)
            self._close_log("sensor recovery: %s" % ",".join(recovered_names))
            try:
                self._open_log()
                self._marker(recfmt.MARK_SENSOR_RECOVER,
                             "continuation after sensor recovery: %s" %
                             ",".join(recovered_names))
            except Exception as exc:
                self.logging_errors += 1
                self.errors += 1
                self.last_error = "log rotation after sensor recovery: %r" % (exc,)
                self.log(self.last_error)

    def _configure_imu(self, odr_hz):
        if not self.imu:
            return False
        try:
            self.imu.fifo_bypass()
            self.imu.set_accel(config.IMU_ACCEL_FS_G, odr_hz)
            self.imu.set_gyro(config.IMU_GYRO_FS_DPS, odr_hz)
            self.imu.fifo_configure(
                watermark_words=config.IMU_FIFO_WATERMARK,
                bdr_hz=odr_hz, timestamp_decim=2)
            return True
        except Exception as exc:
            self._record_error("IMU configure", exc)
            self.imu = None
            return False

    def _open_log(self):
        path = next_log_path()
        header = build_header(
            imu=self._sensor_snapshot("imu", self.imu),
            baro=self._sensor_snapshot("bme280", self.baro),
            sen65=self._sensor_snapshot("sen65", self.sen),
            mission={
                "auto_stop": config.MISSION_AUTO_STOP,
                "liftoff_accel_g": config.LIFTOFF_ACCEL_G,
                "liftoff_dwell_ms": config.LIFTOFF_DWELL_MS,
                "altitude_source": "barometric AGL",
                "mission_mode": self.mode,
                "sensor_availability_at_open": {
                    name: health["available"]
                    for name, health in self.sensor_health.items()
                },
            })
        candidate = BinaryLogger(
            path, clock, flush_every=config.LOG_FLUSH_EVERY).open(header)
        try:
            candidate.write_marker(clock.us(), recfmt.MARK_LOG_START,
                                   "%s session opened" % self.mode)
        except Exception:
            try:
                candidate.close()
            except Exception:
                pass
            raise
        self.logger = candidate
        self.log_close_verified = False
        self.safe_to_power_off = False
        self.log("logging to %s" % path)

    def _sensor_snapshot(self, name, sensor):
        if sensor is None:
            return None
        try:
            return sensor.config_snapshot()
        except Exception as exc:
            self._record_error("%s snapshot" % name.upper(), exc)
            return {"available": False, "snapshot_error": repr(exc)}

    def _close_log(self, reason):
        self.log_close_verified = False
        if self.logger:
            path = self.logger.path
            error = None
            try:
                self.logger.write_marker(clock.us(), recfmt.MARK_LOG_STOP,
                                         reason)
                self.logger.flush()
            except Exception as exc:
                error = exc
            try:
                self.logger.close()
            except Exception as exc:
                error = error or exc
            finally:
                self.logger = None
            if error is not None:
                self._record_error("SD close", error)
                return path
            self.log_close_verified = True
            return path
        # There is no open handle left to corrupt on power removal.
        self.log_close_verified = True
        return None

    def _enter_idle(self, reason="user reset"):
        if self.imu:
            # Stop production before asking for a complete drain. Otherwise a
            # fast FIFO can keep receiving words and never become empty.
            try:
                self.imu.power_down_accel()
                self.imu.power_down_gyro()
                self._drain_fifo(all_available=True)
                self.imu.fifo_bypass()
            except Exception as exc:
                self._record_error("IMU idle transition", exc)
        if self.sen and self.sen.measuring:
            try:
                self.sen.stop_measurement()
            except Exception as exc:
                self._record_error("SEN65 stop", exc)
        self._close_log("return to idle: %s" % reason)

        self.launch_us = None
        self.complete_us = None
        self.liftoff.reset()
        self.altitude = BarometricAltitude(config.VERTICAL_SPEED_TAU_S)
        self._last_baro_ms = -1000
        self._last_sen_ms = -1000
        self._last_sync_ms = -1000
        self._configure_imu(config.PAD_IMU_ODR_HZ)
        self._open_log()
        self.state = PAD_IDLE
        self.state_message = ("waiting for liftoff" if self.imu else
                              "logging available sensors; IMU unavailable, use Simulate liftoff")
        self._sample_baro(force=True)
        self._marker(recfmt.MARK_BOOT, "entered PAD_IDLE: %s" % reason)

    def _enter_continuous(self, reason="power on"):
        self.launch_us = self._boot_us
        self.complete_us = None
        self.altitude = BarometricAltitude(config.VERTICAL_SPEED_TAU_S)
        self._configure_imu(config.PAD_IMU_ODR_HZ)
        self._open_log()
        self.state_message = "starting continuous all-sensor mission"
        if self.sen and not self.sen.measuring:
            try:
                self.sen.start_measurement()
                self._sensor_ok("sen65")
            except Exception as exc:
                self._record_error("SEN65 start", exc)
        self._drain_fifo(all_available=True)
        self._configure_imu(config.IMU_ODR_HZ)
        if self.imu:
            self._log_write("write_config", clock.us(), self.imu.config_snapshot())
        self.state = MISSION_ACTIVE
        self._sample_baro(force=True)
        self._marker(recfmt.MARK_BOOT,
                     "continuous mission from power cycle: %s" % reason)
        self.log("MISSION_ACTIVE continuous; no automatic stop")

    def start_mission(self, source="accelerometer"):
        if self.state == MISSION_COMPLETE:
            return self._start_new_log_mission(source)
        if self.state != PAD_IDLE:
            return {"ok": False, "error": "mission can only start from PAD_IDLE"}
        now = clock.us()
        # Record the trigger immediately. If a simulated launch is pressed
        # just after boot, SEN65 may still owe its 1.4 s restart guard. Keep
        # the IMU in its low-rate pad mode while that wait runs; switching to
        # high rate first can fill and overrun the FIFO before tick() resumes.
        self._marker(recfmt.MARK_USER,
                     "LIFTOFF source=%s auto_stop=false" % source)
        if self.sen and not self.sen.measuring:
            try:
                self.sen.start_measurement()
                self._sensor_ok("sen65")
            except Exception as exc:
                self._record_error("SEN65 start", exc)
        self._drain_fifo(all_available=True)
        self._configure_imu(config.IMU_ODR_HZ)
        if self.imu:
            self._log_write("write_config", clock.us(), self.imu.config_snapshot())
        self.launch_us = now
        self.state = MISSION_ACTIVE
        self.state_message = "mission running"
        self.log("MISSION_ACTIVE (%s); no automatic stop" % source)
        return {"ok": True, "state": self.state, "auto_stop": False}

    def _start_new_log_mission(self, source):
        """Start a fresh numbered BIN from the safely stopped IDLE state."""
        self.maintenance_sampling = False
        self._recover_sensors(force=True)
        self.launch_us = clock.us()
        self.complete_us = None
        self.liftoff.reset()
        self.safe_to_power_off = False
        self.log_close_verified = False
        try:
            self._open_log()
        except Exception as exc:
            self.logging_errors += 1
            self.errors += 1
            self.last_error = "new mission log: %r" % (exc,)
            self.state_message = "IDLE - could not open a new BIN"
            self.safe_to_power_off = True
            return {"ok": False, "error": self.last_error}
        self.state = MISSION_ACTIVE
        if self.sen and not self.sen.measuring:
            try:
                self.sen.start_measurement()
                self._sensor_ok("sen65")
            except Exception as exc:
                self._record_error("SEN65 start", exc)
        self._drain_fifo(all_available=True)
        self._configure_imu(config.IMU_ODR_HZ)
        if self.imu:
            self._log_write("write_config", clock.us(), self.imu.config_snapshot())
        self._sample_baro(force=True)
        self._marker(recfmt.MARK_USER, "MISSION START source=%s" % source)
        self.state_message = "mission active and recording"
        self.log("MISSION_ACTIVE (%s); new BIN; no automatic stop" % source)
        return {"ok": True, "state": self.state, "auto_stop": False,
                "log": self.logger.path if self.logger else None}

    def complete_mission(self, reason="dashboard stop"):
        if self.state != MISSION_ACTIVE:
            return False
        return self._stop_and_close(reason)

    def _stop_and_close(self, reason):
        # Freeze the producer first, preserve the finite FIFO tail, then write
        # and flush the stop marker. Sensor shutdown errors cannot prevent the
        # BIN close attempt.
        if self.imu:
            try:
                self.imu.power_down_accel()
                self.imu.power_down_gyro()
                self._drain_fifo(all_available=True)
                self.imu.fifo_bypass()
            except Exception as exc:
                self._record_error("IMU stop", exc)
        if self.sen and self.sen.measuring:
            try:
                self.sen.stop_measurement()
            except Exception as exc:
                self._record_error("SEN65 stop", exc)
        self.complete_us = clock.us()
        self.maintenance_sampling = False
        self._close_log("MISSION_COMPLETE: %s" % reason)
        self.state = MISSION_COMPLETE
        self.safe_to_power_off = bool(self.log_close_verified and self.logger is None)
        if self.safe_to_power_off:
            self.state_message = "SAFE TO POWER OFF - binary file flushed and closed"
        else:
            self.state_message = "STOPPED, but SD close was not verified - keep power on"
        self.log("MISSION_COMPLETE: %s; safe=%s" %
                 (reason, self.safe_to_power_off))
        return self.safe_to_power_off

    # --------------------------------------------------------------- sampling
    def _record_error(self, where, exc):
        self.errors += 1
        self.last_error = "%s: %r" % (where, exc)
        self.log(self.last_error)
        name = self._sensor_name(where)
        if name:
            health = self.sensor_health[name]
            health["errors"] += 1
            health["consecutive_errors"] += 1
            health["last_error"] = repr(exc)
            health["available"] = False
            if health["consecutive_errors"] == 1:
                self._marker(recfmt.MARK_SENSOR_FAIL, self.last_error)
            if health["consecutive_errors"] >= config.SENSOR_ERROR_REINIT_COUNT:
                if name == "imu":
                    self.imu = None
                elif name == "bme280":
                    self.baro = None
                elif name == "sen65":
                    self.sen = None

    @staticmethod
    def _sensor_name(where):
        upper = where.upper()
        if "IMU" in upper or "TIME SYNC" in upper:
            return "imu"
        if "BME" in upper or "BARO" in upper:
            return "bme280"
        if "SEN65" in upper:
            return "sen65"
        return None

    def _sensor_ok(self, name, recovered=False):
        health = self.sensor_health[name]
        was_failed = health["consecutive_errors"] > 0 or not health["available"]
        health["available"] = True
        health["consecutive_errors"] = 0
        health["last_error"] = None
        health["last_ok_us"] = clock.us()
        if recovered and was_failed:
            self._marker(recfmt.MARK_SENSOR_RECOVER,
                         "%s recovered and rejoined logging" % name)

    def _log_write(self, method, *args):
        if not self.logger:
            return False
        try:
            getattr(self.logger, method)(*args)
            return True
        except Exception as exc:
            self.logging_errors += 1
            self.errors += 1
            self.last_error = "SD logger %s: %r" % (method, exc)
            self.log(self.last_error)
            try:
                self.logger.close()
            except Exception:
                pass
            self.logger = None
            return False

    def _drain_fifo(self, all_available=False):
        if not self.imu or not self.logger:
            return 0
        total = 0
        try:
            drains = 0
            while drains < 64:
                level, hit, overrun, _full = self.imu.fifo_status()
                if overrun:
                    self.fifo_overruns += 1
                if level <= 0:
                    break
                if not all_available and not hit and level < config.IMU_FIFO_WATERMARK:
                    break
                n = min(level, config.IMU_FIFO_WATERMARK)
                self.imu.fifo_read_into(self._fifo_buf, n)
                if not self._log_write("write_imu_fifo", clock.us(),
                                       self._fifo_buf, n):
                    break
                total += n
                drains += 1
                # During normal operation, clear up to four full chunks before
                # touching the slower BME280/SEN65/dashboard paths. This is
                # bounded so those paths still receive service.
                if not all_available and drains >= 4:
                    break
        except Exception as exc:
            self._record_error("IMU FIFO", exc)
        return total

    def _sample_imu(self, force=False):
        if not self.imu:
            return
        now_ms = time.ticks_ms()
        if not force and time.ticks_diff(now_ms, self._last_imu_ms) < 10:
            return
        self._last_imu_ms = now_ms
        try:
            values = self.imu.read()
            ax, ay, az, gx, gy, gz = values
            amag = math.sqrt(ax * ax + ay * ay + az * az)
            self._last_imu = {
                "ax_g": ax, "ay_g": ay, "az_g": az,
                "gx_dps": gx, "gy_dps": gy, "gz_dps": gz,
                "accel_mag_g": amag,
            }
            self._sensor_ok("imu")
            if self.state == PAD_IDLE and self.liftoff.update(amag, now_ms):
                self.start_mission("accelerometer")
        except Exception as exc:
            self._record_error("IMU sample", exc)

    def _sample_baro(self, force=False):
        if not self.baro:
            return
        now_ms = time.ticks_ms()
        rate = (config.FLIGHT_BARO_RATE_HZ if self.state == MISSION_ACTIVE
                else config.PAD_BARO_RATE_HZ)
        if not force and time.ticks_diff(now_ms, self._last_baro_ms) < 1000 // rate:
            return
        self._last_baro_ms = now_ms
        try:
            adc_p, adc_t, adc_h = self.baro.read_raw()
            now_us = clock.us()
            temp_c, t_fine = compensate_t(adc_t, self.baro._cal)
            pressure_pa = compensate_p(adc_p, t_fine, self.baro._cal)
            humidity = (compensate_h(adc_h, t_fine, self.baro._cal)
                        if self.baro.has_humidity else None)
            altitude_m, vertical_speed = self.altitude.update(pressure_pa, now_us)
            self._last_bme = {
                "temperature_c": temp_c,
                "pressure_pa": pressure_pa,
                "humidity_pct": humidity,
                "altitude_agl_m": altitude_m,
                "vertical_speed_mps": vertical_speed,
            }
            self._sensor_ok("bme280")
            if self.logger:
                self._log_write("write_baro", now_us, adc_p, adc_t, adc_h)
        except Exception as exc:
            self._record_error("BME280 sample", exc)

    @staticmethod
    def _scale_sen(words):
        out = {}
        for word, channel in zip(words, CHANNELS):
            name, signed, scale, _unit = channel
            if word == (0x7FFF if signed else 0xFFFF):
                out[name] = None
            else:
                value = word - 65536 if signed and word > 32767 else word
                out[name] = value / scale
        return out

    def _sample_sen(self):
        if not self.sen or not self.sen.measuring:
            return
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, self._last_sen_ms) < 1000 // config.SEN65_POLL_RATE_HZ:
            return
        self._last_sen_ms = now_ms
        try:
            if not self.sen.data_ready():
                return
            # Each SEN65 read can hold the I2C bus for about 20 ms. Service
            # the high-rate IMU FIFO between transactions so a nearly-full
            # FIFO cannot overrun while the three SEN65 datasets are read.
            self._drain_fifo()
            words = self.sen.read_raw_words()
            self._drain_fifo()
            raw_words, raw_scaled = self.sen.read_raw_signals()
            self._drain_fifo()
            number_words, number_scaled = self.sen.read_number_concentration()
            self._drain_fifo()
            now_us = clock.us()
            self._last_sen = self._scale_sen(words)
            self._last_sen.update(raw_scaled)
            self._last_sen.update(number_scaled)
            if time.ticks_diff(now_ms, self._last_sen_status_ms) >= 10000:
                self._last_sen_status_ms = now_ms
                self._drain_fifo()
                self._sen_status, flags = self.sen.device_status()
                self._drain_fifo()
                self._last_sen.update(flags)
                self._last_sen["status_raw"] = self._sen_status
            self._sensor_ok("sen65")
            if self.logger:
                self._log_write("write_sen65", now_us, words, self._sen_status)
                self._log_write("write_sen65_raw", now_us, raw_words)
                self._log_write("write_sen65_number", now_us, number_words)
        except Exception as exc:
            self._record_error("SEN65 sample", exc)

    def _write_timesync(self):
        if not self.logger or not self.imu:
            return
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, self._last_sync_ms) < 1000 // config.TIMESYNC_RATE_HZ:
            return
        self._last_sync_ms = now_ms
        try:
            timestamp = self.imu.read_timestamp()
            self._sensor_ok("imu")
            self._log_write("write_timesync", clock.us(), timestamp)
        except Exception as exc:
            self._record_error("time sync", exc)

    def tick(self):
        if self.state == MISSION_COMPLETE:
            if self.maintenance_sampling:
                self._recover_sensors()
                self._sample_imu()
                self._sample_baro()
                self._sample_sen()
            return
        self._recover_logging()
        self._recover_sensors()
        self._drain_fifo()
        self._sample_imu()
        self._sample_baro()
        self._sample_sen()
        self._write_timesync()

    def _recover_logging(self):
        if self.logger:
            return
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, self._last_logger_recovery_ms) < config.LOGGER_RECOVERY_INTERVAL_MS:
            return
        self._last_logger_recovery_ms = now_ms
        try:
            if not storage.is_mounted():
                storage.mount()
            self._open_log()
            self._marker(recfmt.MARK_SENSOR_RECOVER,
                         "SD logging recovered; new continuation file")
        except Exception as exc:
            self.logging_errors += 1
            self.errors += 1
            self.last_error = "SD logger recovery: %r" % (exc,)
            self.log(self.last_error)

    # ------------------------------------------------------------- commands
    def stop_mission(self):
        if self.state == MISSION_ACTIVE:
            safe = self._stop_and_close("dashboard stop")
            return {"ok": safe, "state": self.state,
                    "safe_to_power_off": safe, "message": self.state_message}
        if self.state != PAD_IDLE:
            return {"ok": False, "error": "no open mission log to stop"}
        safe = self._stop_and_close("dashboard stop from PAD_IDLE")
        return {"ok": safe, "state": self.state,
                "safe_to_power_off": safe, "message": self.state_message}

    def set_boot_mode(self, mode):
        if mode not in (config.MISSION_MODE_LAUNCH,
                        config.MISSION_MODE_CONTINUOUS):
            return {"ok": False, "error": "unknown mission mode"}
        temp_path = config.MISSION_MODE_FILE + ".tmp"
        try:
            with open(temp_path, "w") as target:
                json.dump({"mode": mode}, target)
                target.flush()
            try:
                os.remove(config.MISSION_MODE_FILE)
            except OSError:
                pass
            os.rename(temp_path, config.MISSION_MODE_FILE)
            self.next_boot_mode = mode
            self._marker(recfmt.MARK_USER,
                         "next boot mission mode=%s" % mode)
            return {"ok": True, "next_boot_mode": mode,
                    "note": "takes effect on the next reset or power cycle"}
        except Exception as exc:
            return {"ok": False, "error": repr(exc)}

    def return_to_idle(self):
        self._enter_idle("dashboard command")
        return {"ok": True, "state": self.state,
                "log": self.logger.path if self.logger else None}

    def zero_altitude(self):
        ok = self.altitude.zero()
        if ok:
            self._marker(recfmt.MARK_USER, "barometric AGL zero pressure=%.2f Pa" %
                         self.altitude.reference_pa)
        return {"ok": ok, "reference_pa": self.altitude.reference_pa}

    def start_sen65(self):
        if not self.sen:
            return {"ok": False, "error": "SEN65 unavailable; automatic recovery is active"}
        if self.state == MISSION_ACTIVE:
            return {"ok": False, "error": "SEN65 is mission-controlled while active"}
        try:
            if not self.sen.measuring:
                self.sen.start_measurement()
            self._sensor_ok("sen65")
            if self.state == MISSION_COMPLETE:
                self.maintenance_sampling = True
        except Exception as exc:
            self._record_error("SEN65 manual start", exc)
            return {"ok": False, "error": repr(exc)}
        self._marker(recfmt.MARK_USER, "manual SEN65 start")
        return {"ok": True, "measuring": self.sen.measuring}

    def stop_sen65(self):
        if not self.sen:
            return {"ok": False, "error": "SEN65 unavailable"}
        if self.state == MISSION_ACTIVE:
            return {"ok": False, "error": "SEN65 is mission-controlled while active"}
        try:
            if self.sen.measuring:
                self.sen.stop_measurement()
            if self.state == MISSION_COMPLETE:
                self.maintenance_sampling = False
        except Exception as exc:
            self._record_error("SEN65 manual stop", exc)
            return {"ok": False, "error": repr(exc)}
        self._marker(recfmt.MARK_USER, "manual SEN65 stop")
        return {"ok": True, "measuring": self.sen.measuring}

    def clean_sen65(self):
        if not self.sen:
            return {"ok": False, "error": "SEN65 unavailable"}
        if self.state not in (PAD_IDLE, MISSION_COMPLETE) or self.sen.measuring:
            return {"ok": False, "error": "fan clean requires IDLE and SEN65 stopped"}
        try:
            self.sen.fan_clean(block=False)
        except Exception as exc:
            self._record_error("SEN65 fan clean", exc)
            return {"ok": False, "error": repr(exc)}
        self._marker(recfmt.MARK_TEST, "SEN65 fan clean started; 20 s guard")
        self.last_test = "SEN65 fan clean started (20 s before restart)"
        return {"ok": True, "message": self.last_test}

    def calibrate_gyro(self):
        if not self.imu:
            return {"ok": False, "error": "IMU unavailable; automatic recovery is active"}
        if self.state not in (PAD_IDLE, MISSION_COMPLETE):
            return {"ok": False, "error": "gyro calibration requires IDLE"}
        self.imu.fifo_bypass()
        try:
            self.gyro_bias_dps = self.imu.measure_gyro_bias(samples=500, delay_ms=2)
            text = "gyro bias dps %.4f %.4f %.4f" % self.gyro_bias_dps
            self._marker(recfmt.MARK_TEST, text)
            self.last_test = text
            return {"ok": True, "bias_dps": self.gyro_bias_dps,
                    "note": "stored in this session marker; raw log remains unchanged"}
        except Exception as exc:
            self._record_error("IMU gyro calibration", exc)
            return {"ok": False, "error": repr(exc)}
        finally:
            self._configure_imu(config.PAD_IMU_ODR_HZ)

    def imu_self_test(self):
        if not self.imu:
            return {"ok": False, "error": "IMU unavailable; automatic recovery is active"}
        if self.state not in (PAD_IDLE, MISSION_COMPLETE):
            return {"ok": False, "error": "IMU self-test requires IDLE"}
        self.imu.fifo_bypass()
        try:
            accel = self.imu.self_test_accel()
            gyro = self.imu.self_test_gyro()
            result = {"ok": bool(accel[1] and gyro[1]),
                      "accel_delta_mg": accel[0], "accel_pass": accel[1],
                      "gyro_delta_dps": gyro[0], "gyro_pass": gyro[1]}
            self.last_test = "IMU self-test %s" % ("PASS" if result["ok"] else "FAIL")
            self._marker(recfmt.MARK_TEST, self.last_test)
            return result
        except Exception as exc:
            self._record_error("IMU self-test", exc)
            return {"ok": False, "error": repr(exc)}
        finally:
            self._configure_imu(config.PAD_IMU_ODR_HZ)

    def _marker(self, tag, text):
        if self.logger:
            self._log_write("write_marker", clock.us(), tag, text)

    def set_maintenance_sampling(self, enabled):
        """Run sensors without an open BIN for the optional IDLE live plots."""
        if self.state != MISSION_COMPLETE or self.logger:
            return {"ok": False, "error": "maintenance sampling requires IDLE"}
        enabled = bool(enabled)
        if enabled:
            self._recover_sensors(force=True)
            self._configure_imu(config.PAD_IMU_ODR_HZ)
            if self.sen and not self.sen.measuring:
                try:
                    self.sen.start_measurement()
                    self._sensor_ok("sen65")
                except Exception as exc:
                    self._record_error("SEN65 maintenance start", exc)
        else:
            if self.imu:
                try:
                    self.imu.power_down_accel()
                    self.imu.power_down_gyro()
                    self.imu.fifo_bypass()
                except Exception as exc:
                    self._record_error("IMU maintenance stop", exc)
            if self.sen and self.sen.measuring:
                try:
                    self.sen.stop_measurement()
                except Exception as exc:
                    self._record_error("SEN65 maintenance stop", exc)
        self.maintenance_sampling = enabled
        return {"ok": True, "maintenance_sampling": enabled}

    # --------------------------------------------------------------- status
    def status_snapshot(self):
        now_us = clock.us()
        elapsed = ((now_us - self.launch_us) / 1000000.0
                   if self.launch_us is not None else 0.0)
        imu = self._last_imu or {}
        bme = self._last_bme or {}
        sen = self._last_sen or {}
        log_stats = self.logger.stats() if self.logger else {}
        try:
            sd_free = storage.free_bytes()
        except Exception:
            sd_free = None
        return {
            "state": self.state,
            "message": self.state_message,
            "mission": {
                "mode": self.mode,
                "next_boot_mode": self.next_boot_mode,
                "auto_stop": config.MISSION_AUTO_STOP,
                "elapsed_s": _round(elapsed, 1),
                "liftoff_threshold_g": config.LIFTOFF_ACCEL_G,
                "liftoff_dwell_ms": config.LIFTOFF_DWELL_MS,
                "safe_to_power_off": self.safe_to_power_off,
                "maintenance_sampling": self.maintenance_sampling,
            },
            "altitude": {
                "agl_m": _round(bme.get("altitude_agl_m"), 2),
                "vertical_speed_mps": _round(bme.get("vertical_speed_mps"), 2),
                "zero_pressure_pa": _round(self.altitude.reference_pa, 1),
                "source": "barometer",
            },
            "imu": {
                "accel_mag_g": _round(imu.get("accel_mag_g"), 3),
                "ax_g": _round(imu.get("ax_g"), 3),
                "ay_g": _round(imu.get("ay_g"), 3),
                "az_g": _round(imu.get("az_g"), 3),
                "gx_dps": _round(imu.get("gx_dps"), 2),
                "gy_dps": _round(imu.get("gy_dps"), 2),
                "gz_dps": _round(imu.get("gz_dps"), 2),
                "gyro_bias_dps": self.gyro_bias_dps,
                "odr_hz": self.imu.odr_hz if self.imu else None,
            },
            "bme280": {
                "temperature_c": _round(bme.get("temperature_c"), 2),
                "pressure_pa": _round(bme.get("pressure_pa"), 1),
                "humidity_pct": _round(bme.get("humidity_pct"), 1),
            },
            "sen65": dict(sen, measuring=self.sen.measuring if self.sen else False,
                           warmed_up=self.sen.warmed_up() if self.sen else False),
            "system": {
                "uptime_s": _round((now_us - self._boot_us) / 1000000.0, 1),
                "log_path": log_stats.get("path"),
                "log_bytes": log_stats.get("bytes"),
                "imu_words": log_stats.get("imu_words"),
                "fifo_overruns": self.fifo_overruns,
                "sd_free_bytes": sd_free,
                "heap_free_bytes": gc.mem_free(),
                "errors": self.errors,
                "logging_active": self.logger is not None,
                "logging_errors": self.logging_errors,
                "log_close_verified": self.log_close_verified,
                "safe_to_power_off": self.safe_to_power_off,
                "last_error": self.last_error,
                "last_test": self.last_test,
            },
            "sensor_health": self.sensor_health,
        }

    def list_logs(self):
        return storage.list_data_files()

    def run_forever(self, web=None):
        while True:
            self.tick()
            if web:
                web.poll()
            time.sleep_ms(1)
