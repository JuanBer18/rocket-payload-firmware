# drivers/bme280.py -- pressure / temperature / humidity.
#
# Two read paths on purpose:
#
#   read_raw()          uncompensated ADC counts. This is what gets logged.
#   read_compensated()  real units, for the dashboard only.
#
# Compensation runs on the host because a mistake in the Bosch maths then
# costs a re-run of the decoder rather than a corrupted flight log. The
# per-chip factory coefficients therefore have to travel WITH the log --
# config_snapshot() emits them into the file header, and the decoder reads
# them from there. A log decoded against a different chip's coefficients is
# wrong in a way that looks entirely plausible.
#
# Chip ID 0x60 is a BME280 (has humidity), 0x58 is a BMP280 (does not).
# Both are handled; has_humidity says which one turned up.

import struct
import time

REG_ID = 0xD0
REG_RESET = 0xE0
REG_CTRL_HUM = 0xF2
REG_STATUS = 0xF3
REG_CTRL_MEAS = 0xF4
REG_CONFIG = 0xF5
REG_PRESS = 0xF7

ID_BME280 = 0x60
ID_BMP280 = 0x58

RESET_WORD = 0xB6

MODE_SLEEP = 0x00
MODE_FORCED = 0x01      # one-shot, returns to sleep -- the low-power option
MODE_NORMAL = 0x03

# Oversampling code -> multiplier. 0 skips the channel entirely.
OSR_MULT = {0: 0, 1: 1, 2: 2, 3: 4, 4: 8, 5: 16}

# t_sb code -> standby milliseconds in normal mode.
STANDBY_MS = {0: 0.5, 1: 62.5, 2: 125.0, 3: 250.0, 4: 500.0,
              5: 1000.0, 6: 10.0, 7: 20.0}

IIR_COEFF = {0: 0, 1: 2, 2: 4, 3: 8, 4: 16}

# Pressure-to-altitude sensitivity near sea level, metres per pascal. Used to
# turn a pressure noise figure into an altitude resolution figure.
METRES_PER_PA = 0.083


def measurement_time_ms(osr_t, osr_p, osr_h):
    """Typical conversion time for an oversampling combination.

    From the datasheet timing model. This is what actually caps the rate --
    the headline '157Hz' figure only applies at minimum oversampling, and the
    high-resolution settings that altitude wants land nearer 22Hz.
    """
    t = 1.25
    if osr_t:
        t += 2.3 * OSR_MULT[osr_t]
    if osr_p:
        t += 2.3 * OSR_MULT[osr_p] + 0.575
    if osr_h:
        t += 2.3 * OSR_MULT[osr_h] + 0.575
    return t


class BME280:
    def __init__(self, i2c, addr=0x76):
        self.i2c = i2c
        self.addr = addr
        self.has_humidity = False
        self.chip_id = None
        self.osr_t = 2
        self.osr_p = 5
        self.osr_h = 1
        self.iir = 2
        self.standby = 0
        self.mode = MODE_NORMAL
        self._t_fine = 0.0
        self._buf8 = bytearray(8)
        self._cal = {}

    def _r(self, reg, n=1):
        return self.i2c.readfrom_mem(self.addr, reg, n)

    def _w(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val]))

    # -------------------------------------------------------------- identity
    def read_id(self):
        return self._r(REG_ID)[0]

    def present(self):
        try:
            return self.read_id() in (ID_BME280, ID_BMP280)
        except OSError:
            return False

    def reset(self, wait_ms=10):
        """Soft reset. Coefficients survive in NVM but must be re-read."""
        self._w(REG_RESET, RESET_WORD)
        time.sleep_ms(wait_ms)

    def init(self):
        self.chip_id = self.read_id()
        if self.chip_id not in (ID_BME280, ID_BMP280):
            raise OSError("BME280 chip id 0x%02X, expected 0x60 or 0x58"
                          % self.chip_id)
        self.has_humidity = self.chip_id == ID_BME280
        if not self.has_humidity:
            self.osr_h = 0

        self.reset()
        self._load_calibration()
        self.configure()
        return self

    def _load_calibration(self):
        c = self._r(0x88, 24)
        (t1, t2, t3, p1, p2, p3, p4, p5, p6, p7, p8, p9) = struct.unpack(
            "<HhhHhhhhhhhh", c)
        self._cal = {"T1": t1, "T2": t2, "T3": t3,
                     "P1": p1, "P2": p2, "P3": p3, "P4": p4, "P5": p5,
                     "P6": p6, "P7": p7, "P8": p8, "P9": p9}

        if self.has_humidity:
            h1 = self._r(0xA1)[0]
            h = self._r(0xE1, 7)
            h2, h3 = struct.unpack("<hB", h[0:3])
            e4, e5, e6 = h[3], h[4], h[5]
            h4 = (e4 << 4) | (e5 & 0x0F)
            if h4 > 2047:
                h4 -= 4096
            h5 = (e6 << 4) | (e5 >> 4)
            if h5 > 2047:
                h5 -= 4096
            h6 = h[6] - 256 if h[6] > 127 else h[6]
            self._cal.update({"H1": h1, "H2": h2, "H3": h3,
                              "H4": h4, "H5": h5, "H6": h6})

    # --------------------------------------------------------- configuration
    def configure(self, osr_t=None, osr_p=None, osr_h=None,
                  iir=None, standby=None, mode=None):
        """Apply oversampling / filter / mode. All of it changes at runtime."""
        if osr_t is not None:
            self.osr_t = osr_t
        if osr_p is not None:
            self.osr_p = osr_p
        if osr_h is not None:
            self.osr_h = osr_h if self.has_humidity else 0
        if iir is not None:
            self.iir = iir
        if standby is not None:
            self.standby = standby
        if mode is not None:
            self.mode = mode

        # ctrl_hum only takes effect when ctrl_meas is written afterwards --
        # a documented quirk, and writing them in the other order silently
        # leaves humidity at its previous oversampling.
        if self.has_humidity:
            self._w(REG_CTRL_HUM, self.osr_h & 0x07)
        self._w(REG_CONFIG, ((self.standby & 0x07) << 5) | ((self.iir & 0x07) << 2))
        self._w(REG_CTRL_MEAS,
                ((self.osr_t & 0x07) << 5) | ((self.osr_p & 0x07) << 2) | (self.mode & 0x03))

    def set_mode(self, mode):
        self.mode = mode
        cur = self._r(REG_CTRL_MEAS)[0] & ~0x03
        self._w(REG_CTRL_MEAS, cur | (mode & 0x03))

    def sleep(self):
        self.set_mode(MODE_SLEEP)

    def force_once(self):
        """Trigger one conversion and block until it lands. Sleeps after."""
        self.set_mode(MODE_FORCED)
        budget = measurement_time_ms(self.osr_t, self.osr_p, self.osr_h) * 2 + 5
        deadline = time.ticks_add(time.ticks_ms(), int(budget))
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if not (self._r(REG_STATUS)[0] & 0x08):     # measuring bit clear
                return True
            time.sleep_ms(1)
        return False

    def measuring(self):
        return bool(self._r(REG_STATUS)[0] & 0x08)

    def expected_rate_hz(self):
        """Rate this configuration should sustain in normal mode."""
        period = measurement_time_ms(self.osr_t, self.osr_p, self.osr_h)
        period += STANDBY_MS[self.standby]
        return 1000.0 / period

    # ----------------------------------------------------------------- reads
    def read_raw(self):
        """-> (adc_p, adc_t, adc_h). Uncompensated counts. This gets logged."""
        n = 8 if self.has_humidity else 6
        mv = memoryview(self._buf8)[:n]
        self.i2c.readfrom_mem_into(self.addr, REG_PRESS, mv)
        b = self._buf8
        adc_p = (b[0] << 12) | (b[1] << 4) | (b[2] >> 4)
        adc_t = (b[3] << 12) | (b[4] << 4) | (b[5] >> 4)
        adc_h = ((b[6] << 8) | b[7]) if self.has_humidity else 0
        return (adc_p, adc_t, adc_h)

    def read_compensated(self):
        """-> (temp_c, pressure_pa, humidity_pct or None). Dashboard only."""
        adc_p, adc_t, adc_h = self.read_raw()
        t = compensate_t(adc_t, self._cal)
        self._t_fine = t[1]
        p = compensate_p(adc_p, self._t_fine, self._cal)
        h = compensate_h(adc_h, self._t_fine, self._cal) if self.has_humidity else None
        return (t[0], p, h)

    def config_snapshot(self):
        snap = {
            "part": "BME280" if self.has_humidity else "BMP280",
            "addr": self.addr,
            "chip_id": self.chip_id,
            "has_humidity": self.has_humidity,
            "osr_t": self.osr_t,
            "osr_p": self.osr_p,
            "osr_h": self.osr_h,
            "iir": self.iir,
            "iir_coeff": IIR_COEFF.get(self.iir),
            "standby_code": self.standby,
            "standby_ms": STANDBY_MS.get(self.standby),
            "measurement_ms": measurement_time_ms(self.osr_t, self.osr_p, self.osr_h),
            "expected_rate_hz": self.expected_rate_hz(),
        }
        # The coefficients are per-chip and the decoder cannot work without
        # them. This is the reason config_snapshot exists at all.
        snap["calibration"] = dict(self._cal)
        return snap


# ------------------------------------------------------------- compensation
#
# Free functions taking an explicit coefficient dict, so tools/decode.py can
# import and reuse these against the coefficients stored in a log header
# rather than reimplementing the Bosch algorithm a second time. One
# implementation, two callers.

def compensate_t(adc_t, cal):
    """-> (temp_c, t_fine). t_fine feeds pressure and humidity."""
    v1 = (adc_t / 16384.0 - cal["T1"] / 1024.0) * cal["T2"]
    v2 = ((adc_t / 131072.0 - cal["T1"] / 8192.0) ** 2) * cal["T3"]
    t_fine = v1 + v2
    return (t_fine / 5120.0, t_fine)


def compensate_p(adc_p, t_fine, cal):
    """-> pressure in pascals."""
    v1 = t_fine / 2.0 - 64000.0
    v2 = v1 * v1 * cal["P6"] / 32768.0
    v2 = v2 + v1 * cal["P5"] * 2.0
    v2 = v2 / 4.0 + cal["P4"] * 65536.0
    v1 = (cal["P3"] * v1 * v1 / 524288.0 + cal["P2"] * v1) / 524288.0
    v1 = (1.0 + v1 / 32768.0) * cal["P1"]
    if v1 == 0:
        return 0.0
    p = 1048576.0 - adc_p
    p = (p - v2 / 4096.0) * 6250.0 / v1
    v1 = cal["P9"] * p * p / 2147483648.0
    v2 = p * cal["P8"] / 32768.0
    return p + (v1 + v2 + cal["P7"]) / 16.0


def compensate_h(adc_h, t_fine, cal):
    """-> relative humidity percent, clamped to 0..100."""
    if "H1" not in cal:
        return None
    h = t_fine - 76800.0
    h = (adc_h - (cal["H4"] * 64.0 + cal["H5"] / 16384.0 * h)) * (
        cal["H2"] / 65536.0 * (
            1.0 + cal["H6"] / 67108864.0 * h * (
                1.0 + cal["H3"] / 67108864.0 * h)))
    h = h * (1.0 - cal["H1"] * h / 524288.0)
    return min(100.0, max(0.0, h))


def altitude_agl_m(pressure_pa, pad_pressure_pa):
    """Height above the pad, in metres.

    Deliberately AGL rather than ASL. Absolute altitude needs a sea-level
    reference that this board has no way to know; height above the launch
    point is both what the mission actually wants and what can be computed
    from a pad reading taken at arm time.
    """
    if pad_pressure_pa <= 0 or pressure_pa <= 0:
        return None
    return 44330.0 * (1.0 - (pressure_pa / pad_pressure_pa) ** 0.1903)
