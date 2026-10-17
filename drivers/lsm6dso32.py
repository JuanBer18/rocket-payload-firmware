# drivers/lsm6dso32.py -- accel + gyro, with FIFO, self-test, on-chip
# temperature and the hardware timestamp counter.
#
# v1 read six registers in a loop and nothing else. Three capabilities it
# never touched are what this rig is built around:
#
#   FIFO       -- lets the sensor buffer at its own ODR while Python drains in
#                 bulk. This is what decouples achievable log rate from
#                 interpreter speed.
#   TIMESTAMP  -- a 25us hardware counter. Samples get placed on the timeline
#                 by the sensor's clock, so jitter in when the drain happens
#                 stops mattering.
#   SELF_TEST  -- a real go/no-go on a part that has taken 30g.
#
# The scale factors below are the datasheet values but must be confirmed
# empirically (Phase 1a): at rest, accel magnitude must read 1.000g at EVERY
# full-scale setting. If it does not, the factor is wrong.

import struct
import time

# ---------------------------------------------------------------- registers
FIFO_CTRL1 = 0x07
FIFO_CTRL2 = 0x08
FIFO_CTRL3 = 0x09
FIFO_CTRL4 = 0x0A
INT1_CTRL = 0x0D
WHO_AM_I = 0x0F
CTRL1_XL = 0x10
CTRL2_G = 0x11
CTRL3_C = 0x12
CTRL4_C = 0x13
CTRL5_C = 0x14
CTRL6_C = 0x15
CTRL7_G = 0x16
CTRL9_XL = 0x18
CTRL10_C = 0x19
STATUS_REG = 0x1E
OUT_TEMP_L = 0x20
OUTX_L_G = 0x22
OUTX_L_A = 0x28
FIFO_STATUS1 = 0x3A
FIFO_STATUS2 = 0x3B
TIMESTAMP0 = 0x40
FIFO_DATA_OUT_TAG = 0x78

EXPECTED_WHOAMI = 0x6C

# ------------------------------------------------------------------- coding
# ODR nibble, shared by CTRL1_XL and CTRL2_G. 0x0 powers the sensor down.
ODR = {
    0: 0x0, 12: 0x1, 26: 0x2, 52: 0x3, 104: 0x4, 208: 0x5,
    416: 0x6, 833: 0x7, 1666: 0x8, 3332: 0x9, 6664: 0xA,
}
ODR_HZ = {v: k for k, v in ODR.items()}

# FIFO batch data rate, same values but its own nibble in FIFO_CTRL3.
BDR = {
    0: 0x0, 12: 0x1, 26: 0x2, 52: 0x3, 104: 0x4, 208: 0x5,
    416: 0x6, 833: 0x7, 1666: 0x8, 3332: 0x9, 6664: 0xA,
}

# LSM6DSO32 accel full-scale codes. Note the ordering is NOT monotonic --
# 0x1 is 32g, not 8g. Assuming a sane ordering here is a real and expensive
# bug, and it is why this table is explicit rather than computed.
ACCEL_FS = {4: 0x0, 32: 0x1, 8: 0x2, 16: 0x3}
ACCEL_MG_PER_LSB = {4: 0.122, 8: 0.244, 16: 0.488, 32: 0.976}

GYRO_FS = {250: 0x0, 500: 0x1, 1000: 0x2, 2000: 0x3}
GYRO_MDPS_PER_LSB = {250: 8.75, 500: 17.50, 1000: 35.0, 2000: 70.0}

TEMP_LSB_PER_C = 256.0
TEMP_OFFSET_C = 25.0

TIMESTAMP_RES_US = 25.0

# FIFO_MODE bits [2:0] of FIFO_CTRL4
FIFO_BYPASS = 0x0
FIFO_MODE_FIFO = 0x1        # fills then stops
FIFO_CONTINUOUS = 0x6       # overwrites oldest -- what logging wants

# FIFO tag values, from bits [7:3] of the tag byte
TAG_GYRO = 0x01
TAG_ACCEL = 0x02
TAG_TEMP = 0x03
TAG_TIMESTAMP = 0x04
TAG_CFG_CHANGE = 0x05

TAG_NAMES = {
    TAG_GYRO: "gyro",
    TAG_ACCEL: "accel",
    TAG_TEMP: "temp",
    TAG_TIMESTAMP: "timestamp",
    TAG_CFG_CHANGE: "cfg_change",
}

WORD_LEN = 7                # 1 tag byte + 6 data bytes

# Self-test pass windows, in mg and dps. UNVERIFIED -- these are the usual
# LSM6DSO32 figures but Phase 1a must confirm them against the datasheet
# before a failure here is treated as a dead part.
ST_ACCEL_MIN_MG = 50.0
ST_ACCEL_MAX_MG = 1700.0
ST_GYRO_MIN_DPS = 150.0
ST_GYRO_MAX_DPS = 700.0


class LSM6DSO32:
    def __init__(self, i2c, addr=0x6B, accel_fs_g=32, gyro_fs_dps=2000,
                 odr_hz=1666):
        self.i2c = i2c
        self.addr = addr
        self.accel_fs_g = accel_fs_g
        self.gyro_fs_dps = gyro_fs_dps
        self.odr_hz = odr_hz

        self._buf6 = bytearray(6)
        self._buf12 = bytearray(12)
        self._buf2 = bytearray(2)
        self._buf4 = bytearray(4)
        self._refresh_scales()

    def _refresh_scales(self):
        # g per LSB and dps per LSB, precomputed -- read paths are hot.
        self._a_scale = ACCEL_MG_PER_LSB[self.accel_fs_g] / 1000.0
        self._g_scale = GYRO_MDPS_PER_LSB[self.gyro_fs_dps] / 1000.0

    # ------------------------------------------------------------- bus prims
    def _w(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val]))

    def _r(self, reg, n=1):
        return self.i2c.readfrom_mem(self.addr, reg, n)

    def _r_into(self, reg, buf):
        self.i2c.readfrom_mem_into(self.addr, reg, buf)

    # -------------------------------------------------------------- identity
    def whoami(self):
        return self._r(WHO_AM_I)[0]

    def present(self):
        try:
            return self.whoami() == EXPECTED_WHOAMI
        except OSError:
            return False

    # ----------------------------------------------------------------- setup
    def reset(self, wait_ms=50):
        """Software reset. Self-clearing; poll rather than guess the delay."""
        self._w(CTRL3_C, 0x01)
        deadline = time.ticks_add(time.ticks_ms(), wait_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            try:
                if not (self._r(CTRL3_C)[0] & 0x01):
                    return True
            except OSError:
                pass         # the part NAKs while resetting; that is expected
            time.sleep_ms(1)
        return False

    def boot(self, wait_ms=50):
        """Reload trimming from NVM. Heavier than reset()."""
        self._w(CTRL3_C, 0x80)
        time.sleep_ms(wait_ms)
        return True

    def init(self):
        got = self.whoami()
        if got != EXPECTED_WHOAMI:
            raise OSError(
                "LSM6DSO32 WHO_AM_I=0x%02X, expected 0x%02X at addr 0x%02X "
                "(wrong device, or the 0x6B collision with SEN65 is biting)"
                % (got, EXPECTED_WHOAMI, self.addr))

        self.reset()
        # BDU=1 stops a sample being half-updated mid-read; IF_INC=1 is
        # required for burst and FIFO reads.
        self._w(CTRL3_C, 0x44)
        self.set_accel(self.accel_fs_g, self.odr_hz)
        self.set_gyro(self.gyro_fs_dps, self.odr_hz)
        self.enable_timestamp(True)
        return self

    # ------------------------------------------------------------- rate / FS
    def set_accel(self, fs_g=None, odr_hz=None):
        if fs_g is not None:
            self.accel_fs_g = fs_g
        if odr_hz is not None:
            self.odr_hz = odr_hz
        self._refresh_scales()
        self._w(CTRL1_XL, (ODR[self.odr_hz] << 4) | (ACCEL_FS[self.accel_fs_g] << 2))

    def set_gyro(self, fs_dps=None, odr_hz=None):
        if fs_dps is not None:
            self.gyro_fs_dps = fs_dps
        if odr_hz is not None:
            self.odr_hz = odr_hz
        self._refresh_scales()
        self._w(CTRL2_G, (ODR[self.odr_hz] << 4) | (GYRO_FS[self.gyro_fs_dps] << 2))

    def power_down_accel(self):
        """Accel off, gyro untouched. ODR nibble 0."""
        self._w(CTRL1_XL, ACCEL_FS[self.accel_fs_g] << 2)

    def power_down_gyro(self):
        self._w(CTRL2_G, GYRO_FS[self.gyro_fs_dps] << 2)

    def enable_timestamp(self, on=True):
        cur = self._r(CTRL10_C)[0]
        self._w(CTRL10_C, (cur | 0x20) if on else (cur & ~0x20))

    # ------------------------------------------------------------ polled read
    def read(self):
        """-> (ax, ay, az) g, (gx, gy, gz) dps. One 12-byte burst.

        Gyro comes first in register order (0x22) and accel second (0x28),
        so one read starting at OUTX_L_G gets both.
        """
        self._r_into(OUTX_L_G, self._buf12)
        gx, gy, gz, ax, ay, az = struct.unpack("<6h", self._buf12)
        s = self._a_scale
        t = self._g_scale
        return (ax * s, ay * s, az * s, gx * t, gy * t, gz * t)

    def read_raw(self):
        """Same burst, undecoded counts. For calibration maths."""
        self._r_into(OUTX_L_G, self._buf12)
        gx, gy, gz, ax, ay, az = struct.unpack("<6h", self._buf12)
        return (ax, ay, az, gx, gy, gz)

    def read_temp(self):
        """On-chip die temperature in degC.

        Not ambient -- it reads a few degrees above it. Its value here is as
        the independent variable for gyro bias drift, not as a thermometer.
        """
        self._r_into(OUT_TEMP_L, self._buf2)
        raw = struct.unpack("<h", self._buf2)[0]
        return raw / TEMP_LSB_PER_C + TEMP_OFFSET_C

    def read_timestamp(self):
        """Raw 32-bit hardware counter, 25us per LSB. Wraps every ~29.8 hours."""
        self._r_into(TIMESTAMP0, self._buf4)
        return struct.unpack("<I", self._buf4)[0]

    def data_ready(self):
        s = self._r(STATUS_REG)[0]
        return (bool(s & 0x01), bool(s & 0x02), bool(s & 0x04))  # xl, g, temp

    # ------------------------------------------------------------------ FIFO
    def fifo_configure(self, watermark_words=200, bdr_hz=None,
                       batch_temp=False, timestamp_decim=1,
                       mode=FIFO_CONTINUOUS, int1_watermark=False):
        """Set up FIFO batching.

        watermark_words -- drain trigger, in 7-byte words. Both accel and gyro
            batching means 2 words per sample instant.
        bdr_hz          -- batch rate; defaults to the current ODR. Batching
            slower than the ODR is legal and just decimates.
        timestamp_decim -- 0 off, 1 every batch, 2 every 8th, 3 every 32nd.
            Timestamp words are what let the host reconstruct true timing.
        """
        if bdr_hz is None:
            bdr_hz = self.odr_hz

        # Bypass first: reconfiguring a running FIFO leaves stale words that
        # decode as valid data with wrong timestamps.
        self._w(FIFO_CTRL4, FIFO_BYPASS)

        wtm = watermark_words & 0x1FF
        self._w(FIFO_CTRL1, wtm & 0xFF)
        self._w(FIFO_CTRL2, (wtm >> 8) & 0x01)

        code = BDR[bdr_hz]
        self._w(FIFO_CTRL3, (code << 4) | code)   # gyro high nibble, accel low

        ctrl4 = mode & 0x07
        ctrl4 |= (timestamp_decim & 0x03) << 6
        if batch_temp:
            ctrl4 |= 0x01 << 4                    # temp batched at 12.5Hz
        self._w(FIFO_CTRL4, ctrl4)

        # INT1 is on IO11 and was unused in v1. At high ODR, polling
        # FIFO_STATUS is wasted bus bandwidth -- the watermark interrupt is
        # how the drain gets scheduled without burning I2C on status reads.
        self._w(INT1_CTRL, 0x08 if int1_watermark else 0x00)

    def fifo_bypass(self):
        """Empty and disable the FIFO."""
        self._w(FIFO_CTRL4, FIFO_BYPASS)

    def fifo_status(self):
        """-> (level_words, watermark_hit, overrun, full)

        Overrun means words were lost. That is the number Phase 1a is looking
        for -- the highest ODR at which it stays zero.
        """
        s = self._r(FIFO_STATUS1, 2)
        level = s[0] | ((s[1] & 0x03) << 8)
        flags = s[1]
        return (level, bool(flags & 0x80), bool(flags & 0x40), bool(flags & 0x20))

    def fifo_level(self):
        s = self._r(FIFO_STATUS1, 2)
        return s[0] | ((s[1] & 0x03) << 8)

    def fifo_read_into(self, buf, n_words):
        """Bulk-drain n_words into buf. Returns bytes read.

        This is the hot path and the reason the whole rig works: one I2C
        transaction moves hundreds of samples, and the bytes go to SD
        untouched. No per-sample Python work happens anywhere.

        The FIFO output registers auto-increment and wrap from 0x7E back to
        0x78, so a single burst walks consecutive words.
        """
        n_bytes = n_words * WORD_LEN
        if n_bytes > len(buf):
            raise ValueError("buffer holds %d words, asked for %d"
                             % (len(buf) // WORD_LEN, n_words))
        mv = memoryview(buf)[:n_bytes] if n_bytes != len(buf) else buf
        self._r_into(FIFO_DATA_OUT_TAG, mv)
        return n_bytes

    @staticmethod
    def decode_word(word):
        """-> (tag, counter, x, y, z) for one 7-byte FIFO word.

        Host-side helper, mirrored in tools/decode.py. Kept here so bench
        scripts can sanity-check a drain without leaving the board.
        """
        tag_byte = word[0]
        tag = (tag_byte >> 3) & 0x1F
        cnt = (tag_byte >> 1) & 0x03
        x, y, z = struct.unpack("<3h", word[1:7])
        return (tag, cnt, x, y, z)

    # ------------------------------------------------------------- self-test
    def self_test_accel(self, settle_ms=100, samples=5):
        """-> (dx, dy, dz) in mg, and the pass window.

        Procedure per the datasheet: park at a known ODR and full scale,
        average with self-test off, enable it, average again, and the delta
        must land inside the datasheet window. Deltas outside it mean the
        MEMS structure is damaged -- exactly what a 30g flight might do.
        """
        saved = (self.accel_fs_g, self.gyro_fs_dps, self.odr_hz)
        try:
            self.power_down_gyro()
            self.set_accel(fs_g=4, odr_hz=52)
            time.sleep_ms(settle_ms)
            base = self._avg_accel_raw(samples)

            self._w(CTRL5_C, 0x01)             # ST_XL = positive sign
            time.sleep_ms(settle_ms)
            test = self._avg_accel_raw(samples)
        finally:
            self._w(CTRL5_C, 0x00)
            self.set_accel(fs_g=saved[0], odr_hz=saved[2])
            self.set_gyro(fs_dps=saved[1], odr_hz=saved[2])

        mg = ACCEL_MG_PER_LSB[4]
        delta = tuple(abs(t - b) * mg for t, b in zip(test, base))
        ok = all(ST_ACCEL_MIN_MG <= d <= ST_ACCEL_MAX_MG for d in delta)
        return (delta, ok, (ST_ACCEL_MIN_MG, ST_ACCEL_MAX_MG))

    def self_test_gyro(self, settle_ms=100, samples=5):
        saved = (self.accel_fs_g, self.gyro_fs_dps, self.odr_hz)
        try:
            self.power_down_accel()
            self.set_gyro(fs_dps=2000, odr_hz=208)
            time.sleep_ms(settle_ms)
            base = self._avg_gyro_raw(samples)

            self._w(CTRL5_C, 0x04)             # ST_G = positive sign
            time.sleep_ms(settle_ms)
            test = self._avg_gyro_raw(samples)
        finally:
            self._w(CTRL5_C, 0x00)
            self.set_accel(fs_g=saved[0], odr_hz=saved[2])
            self.set_gyro(fs_dps=saved[1], odr_hz=saved[2])

        dps = GYRO_MDPS_PER_LSB[2000] / 1000.0
        delta = tuple(abs(t - b) * dps for t, b in zip(test, base))
        ok = all(ST_GYRO_MIN_DPS <= d <= ST_GYRO_MAX_DPS for d in delta)
        return (delta, ok, (ST_GYRO_MIN_DPS, ST_GYRO_MAX_DPS))

    def _avg_accel_raw(self, n):
        # First sample after a mode change is discarded -- it may predate the
        # new configuration taking effect.
        self.read_raw()
        sx = sy = sz = 0
        for _ in range(n):
            time.sleep_ms(25)
            ax, ay, az, _gx, _gy, _gz = self.read_raw()
            sx += ax
            sy += ay
            sz += az
        return (sx / n, sy / n, sz / n)

    def _avg_gyro_raw(self, n):
        self.read_raw()
        sx = sy = sz = 0
        for _ in range(n):
            time.sleep_ms(10)
            _ax, _ay, _az, gx, gy, gz = self.read_raw()
            sx += gx
            sy += gy
            sz += gz
        return (sx / n, sy / n, sz / n)

    # ----------------------------------------------------------- calibration
    def measure_gyro_bias(self, samples=2000, delay_ms=2):
        """Average the gyro at rest -> zero-rate offset in dps.

        The board must be dead still. Zero-rate offset is typically around
        +/-1 dps and is NOT repeatable across power cycles, which is why this
        has to run every boot rather than being measured once and stored.
        Phase 1a measures the spread across 5 power cycles to confirm that.
        """
        sx = sy = sz = 0
        for _ in range(samples):
            _ax, _ay, _az, gx, gy, gz = self.read_raw()
            sx += gx
            sy += gy
            sz += gz
            if delay_ms:
                time.sleep_ms(delay_ms)
        t = self._g_scale
        return (sx / samples * t, sy / samples * t, sz / samples * t)

    def config_snapshot(self):
        """Everything the host decoder needs to interpret this session's data."""
        return {
            "part": "LSM6DSO32",
            "addr": self.addr,
            "accel_fs_g": self.accel_fs_g,
            "gyro_fs_dps": self.gyro_fs_dps,
            "odr_hz": self.odr_hz,
            "accel_mg_per_lsb": ACCEL_MG_PER_LSB[self.accel_fs_g],
            "gyro_mdps_per_lsb": GYRO_MDPS_PER_LSB[self.gyro_fs_dps],
            "temp_lsb_per_c": TEMP_LSB_PER_C,
            "temp_offset_c": TEMP_OFFSET_C,
            "timestamp_res_us": TIMESTAMP_RES_US,
        }
