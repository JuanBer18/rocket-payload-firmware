# bench/test_system.py -- board-level capabilities.
#
#     from bench import test_system; test_system.run_all()
#
# Answers "what can I actually reset, and what can I not?" Several of these
# reboot the chip, so they are opt-in rather than part of run_all().

import time

import machine

import config
from bench import _common


def test_firmware(rpt):
    """Confirm the right MicroPython build is on the board."""
    rpt.section("Firmware")
    import sys
    import gc

    rpt.info("MicroPython %s" % ".".join(str(v) for v in sys.implementation.version))
    build = getattr(sys.implementation, "_build", None)
    if build:
        rpt.info("build: %s" % build)
        # The octal build boots on this module but silently fails to init the
        # quad PSRAM, leaving ~223KB instead of ~2MB.
        rpt.check("not the SPIRAM_OCT build", "OCT" not in build.upper(),
                  "this board is an N16R2 -- 2MB QUAD PSRAM. The octal build "
                  "cannot drive a quad part.")

    gc.collect()
    free = gc.mem_free()
    total = free + gc.mem_alloc()
    rpt.measure("gc_mem_free", free, "bytes")
    rpt.measure("gc_heap_total", total, "bytes")
    rpt.check("PSRAM initialised", total >= config.EXPECTED_MEM_FREE_MIN,
              "%d total heap bytes, expected >= %d"
              % (total, config.EXPECTED_MEM_FREE_MIN))

    rpt.measure("cpu_freq_hz", machine.freq(), "Hz")
    rpt.info("reset cause: %s" % _reset_cause_name(machine.reset_cause()))


def _reset_cause_name(cause):
    names = {
        machine.PWRON_RESET: "power-on",
        machine.HARD_RESET: "hard reset",
        machine.WDT_RESET: "watchdog",
        machine.DEEPSLEEP_RESET: "deep sleep wake",
        machine.SOFT_RESET: "soft reset",
    }
    return names.get(cause, "unknown (%s)" % cause)


def test_sensor_resets(rpt):
    """Reset each sensor individually without disturbing the others.

    The point is establishing that a misbehaving sensor can be recovered in
    isolation -- which matters because there is no way to power-cycle one.
    """
    rpt.section("Per-sensor reset")
    from core import buses

    i2c = buses.init_bus(0)
    before = buses.scan(i2c)
    rpt.info("bus before: %s" % " ".join("0x%02X" % a for a in before))

    imu_addr = buses.find_imu(i2c)
    if imu_addr is not None:
        from drivers import lsm6dso32 as L
        imu = L.LSM6DSO32(i2c, addr=imu_addr)
        elapsed = _common.stopwatch()
        ok = imu.reset()
        rpt.check("IMU SW_RESET", ok, "%.1f ms" % (elapsed() / 1000.0))
        rpt.check("IMU answers after its own reset",
                  imu.whoami() == L.EXPECTED_WHOAMI)
    else:
        rpt.skip("IMU reset", "no LSM6DSO32 on the bus")

    baro_id = buses.probe_baro(i2c)
    if baro_id is not None:
        from drivers import bme280 as B
        baro = B.BME280(i2c, addr=config.BARO_ADDR)
        baro.init()
        elapsed = _common.stopwatch()
        baro.reset()
        rpt.check("BME280 soft reset", baro.present(),
                  "%.1f ms" % (elapsed() / 1000.0))
    else:
        rpt.skip("BME280 reset", "no barometer on the bus")

    after = buses.scan(i2c)
    rpt.check("all devices still present after individual resets",
              after == before,
              "before %s, after %s" % (before, after))


def test_general_call_reset(rpt):
    """I2C general call (write 0x06 to 0x00). Not all parts honour it."""
    rpt.section("I2C general-call reset")
    from core import buses

    i2c = buses.init_bus(0)
    before = buses.scan(i2c)

    acked = buses.general_call_reset(i2c)
    if acked:
        rpt.check("general call ACKed", True,
                  "at least one device on the bus honours it")
    else:
        rpt.skip("general call ACKed",
                 "unsupported by the connected devices; no ACK at 0x00")
    time.sleep_ms(100)

    after = buses.scan(i2c)
    rpt.check("bus healthy after general call", after == before,
              "before %s, after %s" % (before, after))
    rpt.info("Record which parts this actually resets -- it is a blunt "
             "instrument and cannot target one device.")


def test_bus_recovery(rpt):
    """Can a wedged bus be freed by clocking SCL manually?"""
    rpt.section("Bus recovery")
    from core import buses
    from machine import Pin

    sda = Pin(config.I2C0_SDA, Pin.IN, Pin.PULL_UP)
    rpt.check("SDA idles high", sda.value() == 1,
              "low means a device is holding the bus")

    ok = buses.bus_recovery(0)
    rpt.check("bus recovery routine completes", ok)

    i2c = buses.init_bus(0)
    found = buses.scan(i2c)
    rpt.check("devices still respond after recovery", len(found) > 0,
              " ".join("0x%02X" % a for a in found))
    rpt.info("To test this properly, unplug a sensor mid-read and confirm the "
             "routine frees the bus rather than needing a chip reset.")


def test_power_gating(rpt):
    """Document what is NOT possible, so it is a known constraint.

    There is no load switch on the sensor rail. The only power lever in the
    whole system is the SEN65's idle command.
    """
    rpt.section("Per-sensor power gating")
    rpt.skip("hardware power gating", "does not exist on this board")
    rpt.info("No load switch on the sensor rail. Consequences:")
    rpt.info("  - A sensor that latches up cannot be power-cycled. Soft reset "
             "over I2C is the only recovery.")
    rpt.info("  - The only real power lever is SEN65 idle (3.3 mA) vs "
             "measuring (80-100 mA).")
    rpt.info("  - IMU and BME280 draw their idle current whenever the board "
             "is powered, regardless of ODR.")
    rpt.info("If a hard sensor power-cycle turns out to be needed, that is a "
             "board change -- a MOSFET on the sensor rail -- not firmware.")


def test_watchdog(rpt, timeout_ms=5000):
    """Arm the watchdog and confirm it is running. DOES NOT let it fire."""
    rpt.section("Watchdog")
    rpt.info("Arming WDT at %d ms and feeding it 5 times." % timeout_ms)

    wdt = machine.WDT(timeout=timeout_ms)
    for i in range(5):
        time.sleep_ms(timeout_ms // 3)
        wdt.feed()
        rpt.info("  fed %d/5" % (i + 1))
    rpt.check("watchdog armed and fed without reset", True)
    rpt.warn("The WDT cannot be disabled once armed. This board will reboot "
             "if the main loop ever stops feeding it -- which is the point, "
             "but it means the REPL will also reboot if left idle.")
    rpt.info("To test that it actually fires, call test_watchdog_fires() -- "
             "that one deliberately reboots the board.")
    return wdt


def test_watchdog_fires(timeout_ms=3000):
    """Arm the watchdog and stop feeding it. REBOOTS THE BOARD.

    After it reboots, check machine.reset_cause() == machine.WDT_RESET.
    """
    print("Arming WDT at %d ms and then hanging deliberately." % timeout_ms)
    print("The board should reboot. Afterwards run:")
    print("    import machine; machine.reset_cause() == machine.WDT_RESET")
    machine.WDT(timeout=timeout_ms)
    while True:
        pass


def test_rtc_memory(rpt):
    """RTC memory survives a soft/WDT reset but is cleared by a power cycle.

    That asymmetry is genuinely useful: a valid marker proves power was
    continuous, which no filesystem timestamp can tell you on a board with no
    battery-backed clock.
    """
    rpt.section("RTC memory")
    rtc = machine.RTC()

    existing = rtc.memory()
    if existing:
        rpt.info("found %d bytes already in RTC memory: %r"
                 % (len(existing), existing[:32]))
        rpt.info("Non-empty means this was a reset, not a cold power-on.")
    else:
        rpt.info("RTC memory is empty -- this was a true power cycle.")

    token = b"BENCH-RTC-TEST"
    rtc.memory(token)
    rpt.check("RTC memory writes and reads back", rtc.memory() == token)

    rpt.info("To confirm the asymmetry:")
    rpt.info("  1. machine.reset()   -> memory should still hold the token")
    rpt.info("  2. pull power        -> memory should come back empty")
    rpt.info("That is what lets a watchdog reboot mid-flight be told apart "
             "from a cold boot on the pad.")


def test_timebase_monotonic(rpt, seconds=30):
    """Quick monotonicity check. The real test is the 18-minute wrap."""
    rpt.section("Timebase")
    from core import clock

    last = clock.us()
    violations = 0
    samples = 0
    deadline = time.ticks_add(time.ticks_ms(), seconds * 1000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        now = clock.us()
        if now < last:
            violations += 1
        last = now
        samples += 1

    rpt.measure("timebase_samples", samples)
    rpt.check("clock never goes backwards", violations == 0,
              "%d violations in %d reads" % (violations, samples))
    rpt.warn("ticks_us wraps every ~17.9 minutes. This 30s check does NOT "
             "exercise that. Run test_timebase_wrap() -- it takes 20 minutes "
             "and is the one that matters.")


def test_timebase_wrap(minutes=20, report_every_s=60):
    """Run past the ticks_us wrap and confirm the accumulator holds.

    ~17.9 minutes is the wrap period. This is verification item 3 in the plan
    and the single correctness property everything else depends on: if the
    timebase jumps, every cross-sensor alignment claim is void.
    """
    from core import clock

    print("Running %d minutes to cross the ~17.9 min ticks_us wrap." % minutes)
    print("Do not interrupt. Progress every %ds.\n" % report_every_s)

    start_wall = time.time()
    last = clock.us()
    violations = 0
    reads = 0
    next_report = start_wall + report_every_s
    end = start_wall + minutes * 60

    while time.time() < end:
        now = clock.us()
        reads += 1
        if now < last:
            violations += 1
            print("  !! VIOLATION at %.1f min: %d -> %d"
                  % ((time.time() - start_wall) / 60.0, last, now))
        last = now

        if time.time() >= next_report:
            mins = (time.time() - start_wall) / 60.0
            drift = last / 1e6 - (time.time() - start_wall)
            print("  %5.1f min   clock %10.1f s   wall-clock delta %+.3f s   "
                  "violations %d" % (mins, last / 1e6, drift, violations))
            next_report += report_every_s

    print("\n%s: %d reads, %d violations over %.1f minutes"
          % ("PASS" if violations == 0 else "FAIL", reads, violations,
             (time.time() - start_wall) / 60.0))
    print("Final clock value: %d us = %.1f minutes" % (last, last / 6e7))
    return violations == 0


def run_all():
    rpt = _common.Report("Board-level capabilities")

    test_firmware(rpt)
    test_rtc_memory(rpt)
    test_timebase_monotonic(rpt)
    test_sensor_resets(rpt)
    test_general_call_reset(rpt)
    test_bus_recovery(rpt)
    test_power_gating(rpt)

    rpt.skip("watchdog", "arms an undisableable WDT -- call test_watchdog(rpt)")
    rpt.skip("watchdog fires", "reboots the board -- call test_watchdog_fires()")
    rpt.skip("timebase wrap", "20 min run -- call test_timebase_wrap()")

    rpt.summary()
    return rpt
