# bench/test_sen65.py -- SEN65 characterization.
#
#     from bench import test_sen65; test_sen65.run_all()
#
# The IMU may remain connected when configured on a different I2C bus.
#
# All command codes are now VERIFIED against the SEN6x datasheet (D1 v0.92),
# and the scale factors in table 32 match what v1 guessed. What remains
# genuinely unknown is the device status BIT POSITIONS, which the datasheet
# defers to a separate document -- hence the healthy-part baseline below.

import time

import config
from bench import _common
from drivers import sen65 as S


def setup(rpt=None):
    from core import buses
    i2c = _common.setup_bus(config.SEN65_BUS)

    if buses.probe_imu(i2c, 0x6B):
        raise OSError(
            "An LSM6DSO32 is answering at 0x6B (WHO_AM_I = 0x6C). The IMU is "
            "still on the bus. Disconnect it before testing the SEN65 -- with "
            "both present every read is the wire-AND of two devices.")

    sen = S.SEN65(i2c, addr=config.SEN65_ADDR)
    if not sen.present():
        raise OSError("No SEN65 responding at 0x%02X." % config.SEN65_ADDR)
    return sen


# ------------------------------------------------------------ command probe
def probe_commands(sen, rpt):
    """Confirm every command the datasheet says this part supports.

    All codes are now verified against the SEN6x datasheet (D1 v0.92), so a
    failure here means a wiring or firmware-revision problem rather than a
    wrong guess.
    """
    rpt.section("Command probe")

    probes = (
        ("version", lambda: sen.version()),
        ("product_name", lambda: sen.product_name()),
        ("serial_number", lambda: sen.serial_number()),
        ("device_status", lambda: sen.device_status()),
        ("voc_algorithm_state", lambda: sen.get_voc_state()),
    )

    working = []
    for name, fn in probes:
        try:
            result = fn()
            working.append(name)
            rpt.check("cmd %s" % name, True, repr(result))
        except OSError as e:
            rpt.check("cmd %s" % name, False,
                      "%s -- datasheet says SEN65 supports this, so suspect "
                      "wiring or a firmware revision difference" % e)
        except Exception as e:            # noqa: BLE001 -- probing, want everything
            rpt.check("cmd %s" % name, False, "unexpected: %r" % e)

    rpt.info("Working: %s" % (", ".join(working) if working else "none"))
    rpt.info("Note: 0x8004 (auto-cleaning interval) is a SEN5x command and "
             "does NOT exist on SEN6x. It has been removed from the driver.")
    return working


def test_identity(sen, rpt):
    rpt.section("Identity")
    rpt.measure("sen65_address", "0x%02X" % sen.addr)
    from core import buses
    if buses.imu_collides_with_sen65():
        rpt.warn("Both SEN65 and IMU are configured at 0x6B. They cannot "
                 "share a bus. Check whether the IMU's SA0 strap can be "
                 "pulled low -- that moves it to 0x6A and costs nothing.")


def test_measurement_cycle(sen, rpt):
    """Start / warm-up / read / stop, with the documented timings measured."""
    rpt.section("Measurement cycle")

    sen.stop_measurement()
    time.sleep_ms(100)

    elapsed = _common.stopwatch()
    sen.start_measurement()

    # How long until data-ready actually goes true? Spec says ~1.1s.
    ready_at = None
    deadline = time.ticks_add(time.ticks_ms(), 5000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        try:
            if sen.data_ready():
                ready_at = elapsed() / 1000.0
                break
        except OSError:
            pass
        time.sleep_ms(20)

    if ready_at is None:
        rpt.check("data ready within 5s", False, "never went ready")
        return
    rpt.measure("warmup_to_data_ready_ms", ready_at, "ms")
    rpt.check("data ready within 5s", True, "%.0f ms" % ready_at)
    rpt.info("Driver assumes %d ms. Adjust SEN65_WARMUP_MS if this differs."
             % S.WARMUP_MS)

    raw, scaled = sen.read_values()
    rpt.info("raw words:  %s" % " ".join(str(v) for v in raw))
    for name, value in scaled.items():
        if value is None:
            rpt.info("  %-6s unknown (sensor reported the 'invalid' sentinel)"
                     % name)
        else:
            rpt.measure("sen65_%s" % name, value)

    rpt.info("Scale factors are CONFIRMED against datasheet table 32 and "
             "match what v1 guessed. Raw words are still what get logged -- "
             "see SEN65.log_note() for why that is still right.")

    elapsed = _common.stopwatch()
    sen.stop_measurement()
    stop_ms = elapsed() / 1000.0
    rpt.measure("stop_command_ms", stop_ms, "ms")
    rpt.check("stop takes about the documented 1400 ms",
              1200 < stop_ms < 1800, "%.0f ms" % stop_ms)


def test_rate_limit(sen, rpt, seconds=10):
    """Confirm the 1Hz hardware limit.

    Polling faster just re-reads the same value. Worth proving rather than
    assuming, because it decides whether SEN65 needs its own cadence in the
    logger or can ride along with anything else.
    """
    rpt.section("Rate limit")

    if not sen.measuring:
        sen.start_measurement()
    # Data-ready arrives before every channel has left its startup sentinel.
    # Wait through the documented gas-signal startup before measuring cadence.
    time.sleep_ms(S.GAS_STARTUP_MS + 500)

    reads = 0
    distinct = 0
    last = None
    elapsed = _common.stopwatch()
    while elapsed() < seconds * 1_000_000:
        try:
            words = sen.read_raw_words()
            reads += 1
            if words != last:
                distinct += 1
                last = words
        except OSError:
            pass
        time.sleep_ms(50)

    secs = elapsed() / 1_000_000.0
    rpt.measure("sen65_read_calls_per_s", reads / secs, "Hz")
    rpt.measure("sen65_distinct_values_per_s", distinct / secs, "Hz")
    rpt.check("distinct values arrive at about 1 Hz",
              0.5 < distinct / secs < 1.5,
              "%.2f Hz from %.2f Hz of polling" % (distinct / secs, reads / secs))
    rpt.info("Polling faster than 1 Hz returns the same value again. This is "
             "a hardware limit, not a configuration choice.")


def test_raw_signals(sen, rpt):
    """Raw ticks -- the channel that should actually be logged in flight.

    Command 0x0455. v1's driver used 0x0405, which is the SEN66 command and
    would have failed or returned a different layout on this part.
    """
    rpt.section("Raw signals (0x0455)")

    try:
        words, scaled = sen.read_raw_signals()
        rpt.check("read_raw_signals works", True, "words %s" % (words,))
        for name, value in scaled.items():
            if value is None:
                rpt.info("  %-9s unknown sentinel" % name)
            else:
                rpt.measure("sen65_%s" % name, value)
        rpt.info("VOC/NOx here are unsigned TICK COUNTS with no scale factor "
                 "-- a direct reading with no adaptive algorithm in front of "
                 "it. That is what makes them the right flight channel.")
        if not sen.gas_ready():
            rpt.warn("Less than %.0f s since power-on: VOC/NOx read their "
                     "unknown sentinels regardless. Not a fault."
                     % (S.GAS_STARTUP_MS / 1000.0))
    except OSError as e:
        rpt.check("read_raw_signals works", False, str(e))


def test_number_concentration(sen, rpt):
    """Particle counts per cm3, including PM0.5.

    Command 0x0316. Not in v1 at all. PM0.5 appears nowhere else, and number
    concentration responds differently to low ambient pressure at altitude
    than mass concentration does -- worth logging both.
    """
    rpt.section("Number concentration (0x0316)")

    try:
        words, scaled = sen.read_number_concentration()
        rpt.check("read_number_concentration works", True, "words %s" % (words,))
        for name, value in scaled.items():
            if value is None:
                rpt.info("  %-6s unknown sentinel" % name)
            else:
                rpt.measure("sen65_%s_per_cm3" % name, value)
        rpt.info("PM0.5 is available here and in no other command.")
    except OSError as e:
        rpt.check("read_number_concentration works", False, str(e))


def test_voc_baseline(sen, rpt):
    """The VOC/NOx index problem, and whether it can be worked around.

    The index outputs come from an algorithm that learns a baseline over
    roughly an hour and reports 100 as neutral. On a 10-minute flight from a
    cold boot the index says nothing at all.

    If the algorithm-state command works, the fix is: warm up on the ground,
    save the state, restore it before flight.
    """
    rpt.section("VOC / NOx baseline")

    if not sen.measuring:
        sen.start_measurement()
        time.sleep_ms(S.WARMUP_MS + 200)

    _raw, scaled = sen.read_values()
    voc = scaled.get("voc")
    nox = scaled.get("nox")
    rpt.info("VOC index %s, NOx index %s" % (voc, nox))
    if voc is not None and abs(voc - 100.0) < 1.0:
        rpt.info("VOC sitting at ~100 = the algorithm's neutral default. It "
                 "has not learned a baseline yet. Expect this from a cold "
                 "boot, and expect it for the whole of a short flight.")

    try:
        state = sen.get_voc_state()
        rpt.check("VOC algorithm state readable", True,
                  "%d bytes: %s" % (len(state), state.hex() if hasattr(state, "hex")
                                    else repr(state)))
        rpt.info("The workaround: warm up on the ground for an hour, save "
                 "this blob, restore it before flight, and the index becomes "
                 "meaningful over a short mission.")
        rpt.info("Readable during measurement, so it can be re-saved every "
                 "cycle and survive a sudden power loss.")

        # Writing is IDLE MODE ONLY -- in measurement mode the datasheet says
        # the command is silently ignored, so testing it here would report a
        # false pass.
        was_measuring = sen.measuring
        if was_measuring:
            sen.stop_measurement()
        try:
            sen.set_voc_state(state)
            rpt.check("VOC algorithm state writable (idle mode)", True,
                      "round-tripped")
        except Exception as e:            # noqa: BLE001
            rpt.check("VOC algorithm state writable (idle mode)", False, repr(e))
        if was_measuring:
            sen.start_measurement()

        rpt.info("Datasheet note: state is RETAINED across stop/start. Only "
                 "a device reset or a real power cycle clears it, so a "
                 "duty-cycled mission keeps its baseline for free.")
    except OSError as e:
        rpt.check("VOC algorithm state readable", False,
                  "0x%04X failed: %s" % (S.CMD_VOC_STATE, e))
        rpt.info("Without this, log raw VOC/NOx ticks and ignore the indices.")


def test_power_states(sen, rpt):
    """Idle vs measuring current, and the timings that constrain duty cycling."""
    rpt.section("Power states")
    rpt.info("Put a meter in series with the board. Spec says 3.3 mA idle, "
             "80 mA typical measuring, 100 mA max.")

    sen.stop_measurement()
    time.sleep_ms(200)
    rpt.info("IDLE -- record current now")
    time.sleep(3)

    sen.start_measurement()
    time.sleep_ms(S.WARMUP_MS + 500)
    rpt.info("MEASURING -- record current now")
    time.sleep(3)

    # The restart guard is what makes duty cycling expensive: stop costs
    # 1400ms, and you cannot restart for another 1400ms after that.
    sen.stop_measurement()
    elapsed = _common.stopwatch()
    sen.start_measurement()
    restart_ms = elapsed() / 1000.0
    rpt.measure("stop_to_start_ms", restart_ms, "ms")
    rpt.info("A full off/on cycle costs %.0f ms before data flows again, plus "
             "%d ms warm-up. Any duty-cycling scheme has to live with that."
             % (restart_ms, S.WARMUP_MS))


def test_device_status(sen, rpt):
    """Read the fault register in a known-good state, for a baseline."""
    rpt.section("Device status")
    try:
        raw, flags = sen.device_status()
        rpt.measure("device_status_raw", "0x%08X" % raw)
        any_fault = False
        for name, value in flags.items():
            if value:
                any_fault = True
                rpt.warn("fault flag set: %s" % name)
        rpt.check("no fault flags on a healthy part", not any_fault,
                  "0x%08X" % raw)
        rpt.info("Record this baseline. Bit positions in the driver are "
                 "UNVERIFIED, so a nonzero value here may just mean the "
                 "decode is wrong rather than the part being faulty.")
    except OSError as e:
        rpt.check("device_status readable", False,
                  "command 0x%04X may be wrong: %s" % (S.CMD_DEVICE_STATUS, e))


def test_reset(sen, rpt):
    rpt.section("Reset")
    try:
        sen.reset()
        time.sleep_ms(500)
        rpt.check("device responds after reset", sen.present())
    except OSError as e:
        rpt.check("device reset", False,
                  "command 0x%04X may be wrong: %s" % (S.CMD_DEVICE_RESET, e))


def run_all(skip_slow=True):
    rpt = _common.Report("SEN65 characterization")
    _common.mem_report(rpt)

    sen = setup(rpt)

    test_identity(sen, rpt)
    probe_commands(sen, rpt)
    test_device_status(sen, rpt)
    test_measurement_cycle(sen, rpt)
    test_raw_signals(sen, rpt)
    test_number_concentration(sen, rpt)
    test_rate_limit(sen, rpt)
    test_voc_baseline(sen, rpt)
    test_reset(sen, rpt)

    if skip_slow:
        rpt.skip("power states",
                 "needs an external meter -- call test_power_states(sen, rpt)")
    else:
        test_power_states(sen, rpt)

    try:
        sen.stop_measurement()
    except OSError:
        pass

    rpt.summary()
    rpt.info("")
    rpt.info("FLIGHT NOTE, not a bench result: this is a fan-driven optical "
             "sensor. The fan bearing will see 30g, PM readings are "
             "pressure-dependent at altitude, and the optics need real "
             "airflow. Decide early whether absolute PM numbers are "
             "trustworthy in flight or whether this is a relative measurement.")
    return sen, rpt
