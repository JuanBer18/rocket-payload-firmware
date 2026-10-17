# drivers/sen65.py -- Sensirion SEN65: particulate, RH/T, VOC and NOx.
#
# VERIFIED against the SEN6x datasheet (Sensirion D1 v0.92, December 2025),
# section 4.8 and tables 29-53. Everything below that used to be marked
# UNVERIFIED has now been checked, and three things were WRONG:
#
#   1. Raw values on SEN65 is 0x0455, NOT 0x0405. 0x0405 is the SEN66
#      command. The guess would have NAKed or returned the wrong layout.
#   2. Auto-cleaning interval (0x8004) DOES NOT EXIST on SEN6x. That is a
#      SEN5x command. Removed rather than left as a trap.
#   3. Get Data Ready returns a padding byte plus a bool, not a bitfield.
#
# Two commands the SEN65 has that were missed entirely:
#   0x0316 Read Number Concentration Values -- particles/cm3 for 5 size bins,
#          including PM0.5 which the mass-concentration read does not cover.
#   0xD100 Get Version.
#
# THE SCALE FACTORS ARE NOW CONFIRMED (datasheet table 32) and match what v1
# guessed. Raw ticks are still what get logged -- see log_note() for why that
# is still the right call even with verified factors.
#
# Device-status bit positions are now verified against sections 4.3.1-4.3.8
# of the bundled SEN6x D1 v0.92 datasheet.

import time

import config

# ----------------------------------------------------- commands (VERIFIED)
CMD_START = 0x0021               # send,  50 ms,   idle only
CMD_STOP = 0x0104                # send,  1400 ms, during measurement
CMD_DATA_READY = 0x0202          # read,  20 ms
CMD_READ_VALUES = 0x0446         # read,  20 ms,   SEN65-specific
CMD_READ_RAW = 0x0455            # read,  20 ms,   SEN65/68/69C
CMD_READ_NUMBER_CONC = 0x0316    # read,  20 ms
CMD_TEMP_OFFSET = 0x60B2         # write, 20 ms
CMD_TEMP_ACCEL = 0x6100          # write, 20 ms,   idle only
CMD_PRODUCT_NAME = 0xD014        # read,  20 ms
CMD_SERIAL_NUMBER = 0xD033       # read,  20 ms
CMD_DEVICE_STATUS = 0xD206       # read,  20 ms
CMD_STATUS_CLEAR = 0xD210        # read,  20 ms
CMD_GET_VERSION = 0xD100         # read,  20 ms
CMD_DEVICE_RESET = 0xD304        # send,  1200 ms, idle only
CMD_FAN_CLEAN = 0x5607           # send,  20 ms,   idle only, runs 10 s
CMD_SHT_HEATER = 0x6765          # send,  20 ms,   idle only
CMD_SHT_HEATER_MEAS = 0x6790     # read,  20 ms,   idle only
CMD_VOC_TUNING = 0x60D0          # r/w,   20 ms,   idle only
CMD_VOC_STATE = 0x6181           # read any mode / WRITE IDLE ONLY
CMD_NOX_TUNING = 0x60E1          # r/w,   20 ms,   idle only

# ---------------------------------------------------------------- timings
STOP_EXEC_MS = 1400
RESTART_GUARD_MS = 1400
RESET_EXEC_MS = 1200
WARMUP_MS = config.SEN65_WARMUP_MS

# Fan cleaning runs the fan at maximum for 10 s, and the datasheet requires
# at least another 10 s before starting a measurement.
FAN_CLEAN_RUN_MS = 10_000
FAN_CLEAN_GUARD_MS = 10_000

# VOC and NOx read 0x7FFF/0xFFFF for the first 10-11 s after power-on or
# device reset regardless of anything else. Not a fault -- expect it.
GAS_STARTUP_MS = 11_000

# --------------------------------------------------- channels (table 32)
# (name, signed, scale, unit) -- CONFIRMED, datasheet table 32.
CHANNELS = (
    ("pm1p0", False, 10.0, "ug/m3"),
    ("pm2p5", False, 10.0, "ug/m3"),
    ("pm4p0", False, 10.0, "ug/m3"),
    ("pm10", False, 10.0, "ug/m3"),
    ("rh", True, 100.0, "%"),
    ("temp", True, 200.0, "degC"),
    ("voc", True, 10.0, "index"),
    ("nox", True, 10.0, "index"),
)

# Raw values, table 37. Note the VOC/NOx entries are UNSIGNED ticks with no
# scale factor -- unlike the signed, scaled index values above.
RAW_CHANNELS = (
    ("raw_rh", True, 100.0, "%"),
    ("raw_temp", True, 200.0, "degC"),
    ("raw_voc", False, 1.0, "ticks"),
    ("raw_nox", False, 1.0, "ticks"),
)

# Number concentration, table 39. All uint16, factor 10, particles/cm3.
# PM0.5 appears here and nowhere else.
NUMBER_CHANNELS = (
    ("nc0p5", 10.0), ("nc1p0", 10.0), ("nc2p5", 10.0),
    ("nc4p0", 10.0), ("nc10", 10.0),
)

UNKNOWN_U16 = 0xFFFF
UNKNOWN_I16 = 0x7FFF

# Verified against SEN6x datasheet D1 v0.92, sections 4.3.1-4.3.8.
STATUS_BITS = (
    (21, "fan_speed_warning"),
    (11, "pm_error"),
    (7, "gas_error"),
    (6, "rht_error"),
    (4, "fan_error"),
)


def crc8(data):
    """CRC-8, poly 0x31, init 0xFF. Datasheet section 4.9."""
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _apply_scale(word, signed, scale):
    """Raw word -> scaled value, honouring the 'unknown' sentinels."""
    if signed:
        if word == UNKNOWN_I16:
            return None
        val = word - 65536 if word > 32767 else word
    else:
        if word == UNKNOWN_U16:
            return None
        val = word
    return val / scale if scale != 1.0 else val


class SEN65:
    def __init__(self, i2c, addr=0x6B):
        self.i2c = i2c
        self.addr = addr
        self.measuring = False
        self._stopped_at = None
        self._started_at = None
        self._powered_at = time.ticks_ms()

    # ------------------------------------------------------------ bus prims
    def _cmd(self, cmd):
        self.i2c.writeto(self.addr, bytes([(cmd >> 8) & 0xFF, cmd & 0xFF]))

    def _cmd_write(self, cmd, words):
        out = bytearray([(cmd >> 8) & 0xFF, cmd & 0xFF])
        for w in words:
            hi = (w >> 8) & 0xFF
            lo = w & 0xFF
            out.append(hi)
            out.append(lo)
            out.append(crc8(bytes([hi, lo])))
        self.i2c.writeto(self.addr, out)

    @staticmethod
    def _unpack(raw, expect_words):
        if len(raw) != expect_words * 3:
            raise OSError("SEN65 short read: %d bytes, expected %d"
                          % (len(raw), expect_words * 3))
        words = []
        for i in range(expect_words):
            chunk = raw[i * 3:i * 3 + 2]
            if crc8(chunk) != raw[i * 3 + 2]:
                raise OSError("SEN65 CRC fail on word %d" % i)
            words.append((chunk[0] << 8) | chunk[1])
        return words

    def _query(self, cmd, n_words, delay_ms=20):
        self._cmd(cmd)
        time.sleep_ms(delay_ms)
        return self._unpack(self.i2c.readfrom(self.addr, n_words * 3), n_words)

    # -------------------------------------------------------------- control
    def start_measurement(self):
        """Idle -> measuring. Honours the 1400 ms post-stop guard."""
        if self._stopped_at is not None:
            waited = time.ticks_diff(time.ticks_ms(), self._stopped_at)
            if waited < RESTART_GUARD_MS:
                time.sleep_ms(RESTART_GUARD_MS - waited)
        self._cmd(CMD_START)
        time.sleep_ms(50)
        self.measuring = True
        self._started_at = time.ticks_ms()
        self._stopped_at = None

    def stop_measurement(self):
        """Measuring -> idle. Blocks the documented 1400 ms execution time."""
        self._cmd(CMD_STOP)
        time.sleep_ms(STOP_EXEC_MS)
        self.measuring = False
        self._stopped_at = time.ticks_ms()

    def warmed_up(self):
        if not self.measuring or self._started_at is None:
            return False
        return time.ticks_diff(time.ticks_ms(), self._started_at) >= WARMUP_MS

    def gas_ready(self):
        """False for the first ~11 s after power-on or reset.

        VOC and NOx return their unknown sentinels during that window no
        matter what else is true. Distinguishes 'still starting' from 'faulty'.
        """
        return time.ticks_diff(time.ticks_ms(), self._powered_at) >= GAS_STARTUP_MS

    def data_ready(self):
        """-> True when a new measurement is available.

        Table 29: byte 0 is padding (always 0x00), byte 1 is a bool. Not a
        bitfield -- v1 masked it as one, which happened to work but for the
        wrong reason.
        """
        word = self._query(CMD_DATA_READY, 1)[0]
        return (word & 0xFF) == 0x01

    def reset(self):
        """Device reset. Idle mode only; same effect as a power cycle.

        This clears the VOC algorithm state. A stop/start does NOT -- see
        get_voc_state().
        """
        self._cmd(CMD_DEVICE_RESET)
        time.sleep_ms(RESET_EXEC_MS)
        self.measuring = False
        self._stopped_at = time.ticks_ms()
        self._powered_at = time.ticks_ms()

    # ----------------------------------------------------------------- data
    def read_raw_words(self):
        """-> 8 raw 16-bit words, CRC-checked. THE LOGGING PATH.

        No scaling, no sign interpretation, no sentinel handling. See
        log_note() for why this stays the logged form even now that the
        scale factors are confirmed.
        """
        return tuple(self._query(CMD_READ_VALUES, 8))

    def read_values(self):
        """-> (raw tuple, scaled dict). Dashboard path. Table 32."""
        words = self._query(CMD_READ_VALUES, 8)
        raw = []
        scaled = {}
        for w, (name, signed, scale, _unit) in zip(words, CHANNELS):
            raw.append(w - 65536 if signed and w > 32767 else w)
            scaled[name] = _apply_scale(w, signed, scale)
        return (tuple(raw), scaled)

    def read_raw_signals(self):
        """-> (raw words, scaled dict) for RH / T / VOC ticks / NOx ticks.

        Table 37, command 0x0455. VOC and NOx here are UNSIGNED tick counts
        with no scale factor -- a direct sensor reading with no adaptive
        algorithm in front of it, which is what makes them the right flight
        channel over the index outputs.
        """
        words = self._query(CMD_READ_RAW, 4)
        scaled = {}
        for w, (name, signed, scale, _unit) in zip(words, RAW_CHANNELS):
            scaled[name] = _apply_scale(w, signed, scale)
        return (tuple(words), scaled)

    def read_number_concentration(self):
        """-> (raw words, scaled dict) in particles/cm3. Table 39.

        Five size bins including PM0.5, which the mass-concentration read
        does not expose at all. Number concentration also responds
        differently to the low ambient pressure at altitude than mass
        concentration does, so logging both is worth the 15 bytes.
        """
        words = self._query(CMD_READ_NUMBER_CONC, 5)
        scaled = {}
        for w, (name, scale) in zip(words, NUMBER_CHANNELS):
            scaled[name] = _apply_scale(w, False, scale)
        return (tuple(words), scaled)

    # --------------------------------------------------------------- status
    def device_status(self, clear=False):
        """-> (raw uint32, decoded flags dict). Table 44.

        The uint32 arrives as two CRC-protected words. The decoded SEN65 flag
        positions are verified against datasheet sections 4.3.1-4.3.8.
        """
        words = self._query(CMD_STATUS_CLEAR if clear else CMD_DEVICE_STATUS, 2)
        raw = (words[0] << 16) | words[1]
        flags = {name: bool(raw & (1 << bit))
                 for bit, name in STATUS_BITS}
        return (raw, flags)

    def version(self):
        """-> (major, minor). Table 46."""
        word = self._query(CMD_GET_VERSION, 1)[0]
        return ((word >> 8) & 0xFF, word & 0xFF)

    def product_name(self):
        """-> ASCII string, up to 32 chars. Table 42: 48 bytes = 16 words."""
        return self._read_string(CMD_PRODUCT_NAME, 16)

    def serial_number(self):
        """-> ASCII string, up to 32 chars. Table 43: 48 bytes = 16 words."""
        return self._read_string(CMD_SERIAL_NUMBER, 16)

    def _read_string(self, cmd, n_words):
        words = self._query(cmd, n_words)
        out = bytearray()
        for w in words:
            out.append((w >> 8) & 0xFF)
            out.append(w & 0xFF)
        return bytes(out).split(b"\x00")[0].decode("ascii", "replace")

    # ----------------------------------------------------------- maintenance
    def fan_clean(self, block=False):
        """Spin the fan to maximum for 10 s to clear the optics.

        Idle mode only. The datasheet also requires at least 10 s after the
        run before starting a measurement, so the full cost is ~20 s.
        """
        if self.measuring:
            raise OSError("fan cleaning is idle-mode only; stop measurement first")
        self._cmd(CMD_FAN_CLEAN)
        time.sleep_ms(20)
        if block:
            time.sleep_ms(FAN_CLEAN_RUN_MS + FAN_CLEAN_GUARD_MS)
        else:
            # Make the guard automatic even when not blocking, so a later
            # start_measurement() cannot jump the gun.
            self._stopped_at = time.ticks_add(
                time.ticks_ms(), FAN_CLEAN_RUN_MS + FAN_CLEAN_GUARD_MS
                - RESTART_GUARD_MS)

    def activate_sht_heater(self):
        """200 mW for 1 s to reverse creep at high humidity. Idle mode only."""
        if self.measuring:
            raise OSError("SHT heater is idle-mode only; stop measurement first")
        self._cmd(CMD_SHT_HEATER)
        time.sleep_ms(20)

    # ------------------------------------------------------- algorithm state
    def get_voc_state(self):
        """-> 8-byte VOC algorithm state blob. Table 53.

        This is the answer to the baseline problem. The VOC index comes from
        an algorithm that learns over roughly an hour and reports 100 as
        neutral, so a 10-minute flight from cold produces nothing useful.
        Warm up on the ground, save this blob, restore it before flight.

        Two facts from the datasheet worth knowing:
          - State is RETAINED across a stop/start of measurement. Only a
            device reset or a real power cycle clears it.
          - It can be read during measurement, so it can be re-saved every
            cycle and survive a sudden power loss.
        """
        words = self._query(CMD_VOC_STATE, 4)
        out = bytearray()
        for w in words:
            out.append((w >> 8) & 0xFF)
            out.append(w & 0xFF)
        return bytes(out)

    def set_voc_state(self, blob):
        """Restore a saved VOC baseline. IDLE MODE ONLY.

        The datasheet is explicit: this has NO EFFECT in measurement mode,
        and the restored state is applied once, when the next measurement
        starts. It is also volatile -- a device reset reverts it.
        """
        if len(blob) != 8:
            raise ValueError("VOC state blob must be 8 bytes, got %d" % len(blob))
        if self.measuring:
            raise OSError(
                "set_voc_state is idle-mode only -- in measurement mode the "
                "command is silently ignored. Stop measurement first, then "
                "restore, then start.")
        words = [(blob[i] << 8) | blob[i + 1] for i in range(0, 8, 2)]
        self._cmd_write(CMD_VOC_STATE, words)
        time.sleep_ms(20)

    # ------------------------------------------------------------------ init
    def present(self):
        """Cheap liveness check.

        Cannot distinguish a SEN65 from the IMU while both sit at 0x6B -- an
        ACK proves only that something is there. buses.py owns that problem.
        """
        try:
            self.version()
            return True
        except OSError:
            return False

    def init(self):
        """Leave the part idle. Callers start measurement explicitly."""
        try:
            self.stop_measurement()
        except OSError:
            # A cold part is already idle and may NAK the stop. Harmless.
            self.measuring = False
            self._stopped_at = time.ticks_ms()
        return self

    @staticmethod
    def log_note():
        """Why raw ticks stay the logged form even with verified scaling."""
        return (
            "Scale factors are now confirmed against datasheet table 32, but "
            "raw words remain the logged form for two reasons that verifying "
            "them does not address: (1) the VOC/NOx INDEX values are outputs "
            "of an adaptive algorithm whose baseline is meaningless over a "
            "short flight, so the useful gas channel is raw ticks from 0x0455 "
            "regardless; (2) scaling on the host keeps the flight-side write "
            "path free of per-sample arithmetic, which is the whole reason "
            "this rig can log at kHz rates.")

    def config_snapshot(self):
        return {
            "part": "SEN65",
            "addr": self.addr,
            "channels": [c[0] for c in CHANNELS],
            "scale_factors": {c[0]: c[2] for c in CHANNELS},
            "signed": {c[0]: c[1] for c in CHANNELS},
            "raw_channels": [c[0] for c in RAW_CHANNELS],
            "raw_scale_factors": {c[0]: c[2] for c in RAW_CHANNELS},
            "number_channels": [c[0] for c in NUMBER_CHANNELS],
            "scale_verified": True,
            "scale_source": "Sensirion SEN6x datasheet D1 v0.92, table 32",
            "status_bits_verified": True,
            "logged_as": "raw_ticks",
            "warmup_ms": WARMUP_MS,
            "gas_startup_ms": GAS_STARTUP_MS,
        }
