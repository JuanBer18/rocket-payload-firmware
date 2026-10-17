# core/buses.py -- I2C bring-up, device presence scan, and the address
# collision guard.
#
# THE COLLISION: LSM6DSO32 and SEN65 both answer at 0x6B as this board is
# wired. Two devices sharing an address on one bus is not a soft problem --
# both ACK the address byte and both drive SDA during the data phase, so
# reads come back as the wire-AND of two different answers. That is garbage
# which looks like plausible data, which is the worst possible failure mode
# for a logger.
#
# So this module refuses. If both are configured on the same bus at the same
# address it raises rather than returning something that mostly works. The
# fix is in config.py and is probably free -- the IMU's address is
# strap-selectable and drops to 0x6A if SA0 is pulled low.

from machine import Pin, SoftI2C
import machine
import time

import config


class AddressCollision(Exception):
    """Two devices configured at the same address on the same bus."""


def init_bus(bus_num=0, freq=None):
    """Bring up an I2C bus. Returns the peripheral object."""
    if bus_num == 0:
        sda, scl = config.I2C0_SDA, config.I2C0_SCL
        default_freq = config.I2C0_FREQ
    elif bus_num == 1:
        sda, scl = config.I2C1_SDA, config.I2C1_SCL
        default_freq = config.I2C1_FREQ
        if sda is None or scl is None:
            raise ValueError(
                "I2C1 pins are unset in config.py. They are deliberately "
                "unassigned -- pick them against the schematic rather than "
                "guessing, and only if the SA0 strap fix failed.")
    else:
        raise ValueError("no such bus: %d" % bus_num)

    return machine.I2C(bus_num, sda=Pin(sda), scl=Pin(scl),
                       freq=freq or default_freq)


def soft_bus(bus_num=0, freq=None):
    """Bit-banged fallback.

    Useful when the hardware peripheral latches up: SoftI2C can be
    reconstructed from scratch without a chip reset.
    """
    sda = config.I2C0_SDA if bus_num == 0 else config.I2C1_SDA
    scl = config.I2C0_SCL if bus_num == 0 else config.I2C1_SCL
    return SoftI2C(sda=Pin(sda), scl=Pin(scl), freq=freq or config.I2C0_FREQ)


def scan(i2c):
    """-> sorted list of responding addresses."""
    return sorted(i2c.scan())


def describe_scan(i2c, bus_num=None):
    """Human-readable scan, naming what each address is expected to be."""
    lines = []
    for addr in scan(i2c):
        names = []
        if addr == config.IMU_ADDR and bus_num in (None, config.IMU_BUS):
            names.append("LSM6DSO32 (IMU)")
        if addr == config.BARO_ADDR and bus_num in (None, config.BARO_BUS):
            names.append("BME280/BMP280")
        if addr == config.SEN65_ADDR and bus_num in (None, config.SEN65_BUS):
            names.append("SEN65")
        if addr == 0x6A and not names:
            names.append("LSM6DSO32 (IMU, SA0 low)")
        lines.append("  0x%02X  %s" % (addr, "/".join(names) or "unknown"))
    return "\n".join(lines) if lines else "  (nothing responded)"


def imu_collides_with_sen65():
    return (config.IMU_ADDR == config.SEN65_ADDR
            and config.IMU_BUS == config.SEN65_BUS)


def check_collision(want_imu=True, want_sen65=True):
    """Raise if the requested combination cannot work as configured.

    Called before bringing sensors up. Individual bench scripts request only
    the sensor they test, so they pass this freely -- which is exactly why
    Phase 1 tests each part alone.
    """
    if want_imu and want_sen65 and imu_collides_with_sen65():
        raise AddressCollision(
            "LSM6DSO32 and SEN65 are both at 0x%02X on bus %d. They cannot "
            "share a bus: both would ACK and both would drive SDA, so reads "
            "return the wire-AND of two devices.\n"
            "Fixes, cheapest first:\n"
            "  1. Pull the IMU's SA0/SD0 strap low -> IMU moves to 0x6A. "
            "Set config.IMU_ADDR = 0x6A.\n"
            "  2. If the SEN65 module has an address strap, set "
            "config.SEN65_ADDR.\n"
            "  3. Move SEN65 to a second bus: set config.I2C1_SDA/SCL and "
            "config.SEN65_BUS = 1.\n"
            "Until then, test them one at a time."
            % (config.IMU_ADDR, config.IMU_BUS))


def probe_imu(i2c, addr=None):
    """Is a real LSM6DSO32 at this address? Checks WHO_AM_I, not just an ACK.

    An ACK proves only that something is there. WHO_AM_I == 0x6C proves it is
    the IMU -- which is how you confirm the SA0 strap fix actually worked.
    """
    addr = addr if addr is not None else config.IMU_ADDR
    try:
        return i2c.readfrom_mem(addr, 0x0F, 1)[0] == 0x6C
    except OSError:
        return False


def find_imu(i2c):
    """-> the address the IMU is actually at (0x6A or 0x6B), or None."""
    for addr in (0x6A, 0x6B):
        if probe_imu(i2c, addr):
            return addr
    return None


def probe_baro(i2c, addr=None):
    """-> chip id, or None. 0x60 = BME280, 0x58 = BMP280."""
    addr = addr if addr is not None else config.BARO_ADDR
    try:
        cid = i2c.readfrom_mem(addr, 0xD0, 1)[0]
        return cid if cid in (0x60, 0x58) else None
    except OSError:
        return None


def bus_recovery(bus_num=0, pulses=9):
    """Free a bus where a device is holding SDA low.

    A slave interrupted mid-byte can keep driving SDA and wedge the bus for
    everyone. Clocking SCL manually walks it through the rest of the byte
    until it releases. Returns True if SDA came back high.
    """
    sda_pin = config.I2C0_SDA if bus_num == 0 else config.I2C1_SDA
    scl_pin = config.I2C0_SCL if bus_num == 0 else config.I2C1_SCL

    sda = Pin(sda_pin, Pin.IN, Pin.PULL_UP)
    scl = Pin(scl_pin, Pin.OUT, value=1)

    if sda.value():
        return True

    for _ in range(pulses):
        scl.value(0)
        time.sleep_us(5)
        scl.value(1)
        time.sleep_us(5)
        if sda.value():
            break

    # Manual STOP -- SDA rising while SCL is high -- so any half-finished
    # transaction is properly terminated rather than left dangling.
    sda_out = Pin(sda_pin, Pin.OUT, value=0)
    time.sleep_us(5)
    scl.value(1)
    time.sleep_us(5)
    sda_out.value(1)
    time.sleep_us(5)

    return Pin(sda_pin, Pin.IN, Pin.PULL_UP).value() == 1


def general_call_reset(i2c):
    """I2C general-call reset: write 0x06 to address 0x00.

    Not all parts honour it. Phase 1e records which of these three do.
    """
    try:
        i2c.writeto(0x00, b"\x06")
        return True
    except OSError:
        return False
