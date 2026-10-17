"""Barometric height above launch point and filtered vertical speed."""

from drivers.bme280 import altitude_agl_m


class BarometricAltitude:
    """Small, deterministic estimator used by the dashboard.

    Pressure, not this derived result, is the authoritative flight record.
    That keeps better post-flight filters possible. No accelerometer is mixed
    in here until a tested attitude solution exists: a body-axis acceleration
    is not vertical acceleration once the vehicle tilts.
    """

    def __init__(self, tau_s=0.5):
        self.tau_s = float(tau_s)
        self.reference_pa = None
        self.pressure_pa = None
        self.altitude_m = None
        self.vertical_speed_mps = 0.0
        self._last_alt_m = None
        self._last_t_us = None

    def zero(self, pressure_pa=None):
        pressure_pa = self.pressure_pa if pressure_pa is None else pressure_pa
        if pressure_pa is None or pressure_pa <= 0:
            return False
        self.reference_pa = float(pressure_pa)
        self.altitude_m = 0.0
        self.vertical_speed_mps = 0.0
        self._last_alt_m = 0.0
        self._last_t_us = None
        return True

    def update(self, pressure_pa, t_us):
        if pressure_pa is None or pressure_pa <= 0:
            return self.altitude_m, self.vertical_speed_mps
        self.pressure_pa = float(pressure_pa)
        if self.reference_pa is None:
            self.zero(self.pressure_pa)

        altitude = altitude_agl_m(self.pressure_pa, self.reference_pa)
        if altitude is None:
            return self.altitude_m, self.vertical_speed_mps

        if self._last_t_us is not None:
            dt = (t_us - self._last_t_us) / 1000000.0
            if 0.001 <= dt <= 2.0:
                raw_vz = (altitude - self._last_alt_m) / dt
                alpha = dt / (self.tau_s + dt) if self.tau_s > 0 else 1.0
                self.vertical_speed_mps += alpha * (
                    raw_vz - self.vertical_speed_mps)
            elif dt > 2.0:
                self.vertical_speed_mps = 0.0

        self.altitude_m = altitude
        self._last_alt_m = altitude
        self._last_t_us = t_us
        return altitude, self.vertical_speed_mps

    def snapshot(self):
        return {
            "reference_pa": self.reference_pa,
            "altitude_agl_m": self.altitude_m,
            "vertical_speed_mps": self.vertical_speed_mps,
            "source": "barometric",
        }
