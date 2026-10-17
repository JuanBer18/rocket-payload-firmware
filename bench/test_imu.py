# bench/test_imu.py -- LSM6DSO32 characterization.
#
# Run everything:
#     from bench import test_imu; test_imu.run_all()
#
# Or one test at a time, which is the point of the split:
#     from bench import test_imu
#     imu = test_imu.setup()
#     test_imu.test_rate_fifo(imu)
#
# The SEN65 may remain connected when configured on a different I2C bus.

import time

import config
from bench import _common
from drivers import lsm6dso32 as L


def setup(rpt=None):
    i2c = _common.setup_bus(config.IMU_BUS)
    from core import buses
    found = buses.find_imu(i2c)
    if found is None:
        raise OSError("No LSM6DSO32 found at 0x6A or 0x6B. Check wiring.")
    if found != config.IMU_ADDR:
        print("NOTE: IMU answered at 0x%02X but config.IMU_ADDR is 0x%02X."
              % (found, config.IMU_ADDR))
        if found == 0x6A:
            print("      SA0 is low -- the SEN65 collision is resolved. "
                  "Set config.IMU_ADDR = 0x6A.")
    imu = L.LSM6DSO32(i2c, addr=found,
                      accel_fs_g=config.IMU_ACCEL_FS_G,
                      gyro_fs_dps=config.IMU_GYRO_FS_DPS,
                      odr_hz=config.IMU_ODR_HZ)
    imu.init()
    return imu


# ---------------------------------------------------------------- identity
def test_identity(imu, rpt):
    rpt.section("Identity")
    got = imu.whoami()
    rpt.check("WHO_AM_I == 0x6C", got == L.EXPECTED_WHOAMI, "got 0x%02X" % got)
    rpt.measure("imu_address", "0x%02X" % imu.addr)
    from core import buses
    if not buses.imu_collides_with_sen65():
        rpt.info("IMU and SEN65 are on separate buses -- no address collision.")
    elif imu.addr == 0x6A:
        rpt.info("At 0x6A -- SA0 strap is low, no collision with SEN65.")
    else:
        rpt.warn("At 0x6B -- collides with SEN65. Check whether the SA0 strap "
                 "can be pulled low; that fix costs one solder blob and no code.")


def test_channels(imu, rpt):
    """Enumerate everything the part exposes, including what v1 never read."""
    rpt.section("Channels")

    ax, ay, az, gx, gy, gz = imu.read_raw()
    rpt.info("accel raw   %6d %6d %6d" % (ax, ay, az))
    rpt.info("gyro  raw   %6d %6d %6d" % (gx, gy, gz))

    ax, ay, az, gx, gy, gz = imu.read()
    mag = (ax * ax + ay * ay + az * az) ** 0.5
    rpt.info("accel g     %7.4f %7.4f %7.4f  |a| = %.4f" % (ax, ay, az, mag))
    rpt.info("gyro  dps   %7.3f %7.3f %7.3f" % (gx, gy, gz))

    # Two channels v1 never touched.
    temp = imu.read_temp()
    rpt.measure("die_temp_c", temp, "degC")
    rpt.check("temperature plausible", -40.0 < temp < 85.0, "%.2f degC" % temp)
    rpt.info("This is die temperature, a few degrees above ambient. Its use "
             "is as the x-axis for gyro bias drift, not as a thermometer.")

    t0 = imu.read_timestamp()
    time.sleep_ms(100)
    t1 = imu.read_timestamp()
    ticks = t1 - t0
    elapsed_us = ticks * L.TIMESTAMP_RES_US
    rpt.measure("timestamp_ticks_per_100ms", ticks, "ticks")
    rpt.measure("timestamp_implied_ms", elapsed_us / 1000.0, "ms")
    # 25us nominal resolution over 100ms is 4000 ticks. The internal
    # oscillator is not trimmed tightly, so a few percent off is normal and
    # is exactly why the host fits a linear map rather than trusting 25.0us.
    rpt.check("hardware timestamp advances", ticks > 0, "%d ticks" % ticks)
    rpt.check("timestamp within 20%% of wall clock",
              80.0 < elapsed_us / 1000.0 < 120.0,
              "implied %.2f ms for a 100 ms sleep" % (elapsed_us / 1000.0))


def test_scale_factors(imu, rpt):
    """At rest, |a| must be 1.000g at EVERY full-scale setting.

    This is the cheap check that catches the 4/8/16/32 vs 2/4/8/16 mistake.
    If the accel FS table were wrong, magnitude would come out 2x off at the
    affected settings while still looking like a plausible number.
    """
    rpt.section("Scale factor verification")
    rpt.info("Board must be still and roughly level.")

    saved_fs = imu.accel_fs_g
    results = {}
    try:
        for fs in (4, 8, 16, 32):
            imu.set_accel(fs_g=fs)
            time.sleep_ms(100)
            mags = []
            for _ in range(20):
                ax, ay, az, _gx, _gy, _gz = imu.read()
                mags.append((ax * ax + ay * ay + az * az) ** 0.5)
                time.sleep_ms(10)
            m = _common.mean(mags)
            s = _common.stdev(mags)
            results[fs] = (m, s)
            rpt.measure("accel_mag_fs%dg" % fs, m, "g")
            rpt.measure("accel_noise_fs%dg" % fs, s, "g")
            # 5% covers real zero-g offset without letting a 2x error pass.
            rpt.check("|a| ~ 1.000g at +/-%dg" % fs, 0.95 < m < 1.05,
                      "%.4f g (noise %.4f g)" % (m, s))
    finally:
        imu.set_accel(fs_g=saved_fs)

    if 4 in results and 32 in results:
        n4 = results[4][1]
        n32 = results[32][1]
        rpt.info("Noise at +/-32g is %.1fx that at +/-4g." % (n32 / n4 if n4 else 0))
        rpt.info("If that ratio is large, calibrating offsets at +/-32g is "
                 "mostly fitting noise -- consider sitting at +/-4g on the pad "
                 "and switching to +/-32g at liftoff.")


def test_self_test(imu, rpt):
    """Built-in MEMS self-test. A real go/no-go on a part that has flown."""
    rpt.section("Self-test")
    rpt.info("Board must be still.")

    delta, ok, window = imu.self_test_accel()
    rpt.info("accel delta  %.1f %.1f %.1f mg   window %.0f-%.0f mg"
             % (delta[0], delta[1], delta[2], window[0], window[1]))
    rpt.check("accel self-test", ok,
              "deltas %.0f/%.0f/%.0f mg" % delta)

    delta, ok, window = imu.self_test_gyro()
    rpt.info("gyro  delta  %.1f %.1f %.1f dps  window %.0f-%.0f dps"
             % (delta[0], delta[1], delta[2], window[0], window[1]))
    rpt.check("gyro self-test", ok, "deltas %.0f/%.0f/%.0f dps" % delta)

    rpt.warn("Self-test windows in drivers/lsm6dso32.py are UNVERIFIED. "
             "Confirm against the datasheet before treating a failure here "
             "as a dead part.")


# ------------------------------------------------------------------- rates
def test_rate_polled(imu, rpt, duration_s=5):
    """How fast can we actually poll? This measures the bus and Python.

    Expect a wall near 700Hz at 100kHz I2C and ~2.9kHz at 400kHz -- the
    12-byte burst alone is 1.38ms and 0.35ms respectively.
    """
    rpt.section("Polled rate sweep")
    saved_odr = imu.odr_hz

    try:
        for odr in (104, 416, 833, 1666, 3332, 6664):
            imu.set_accel(odr_hz=odr)
            imu.set_gyro(odr_hz=odr)
            time.sleep_ms(50)

            n = 0
            elapsed = _common.stopwatch()
            budget_us = duration_s * 1_000_000
            while elapsed() < budget_us:
                imu.read_raw()
                n += 1
            secs = elapsed() / 1_000_000.0
            achieved = n / secs
            rpt.measure("polled_hz_at_odr%d" % odr, achieved, "Hz")
            if achieved < odr * 0.95:
                rpt.info("  ODR %d Hz requested, %d Hz achieved -- polling is "
                         "the limit here, not the sensor." % (odr, int(achieved)))
    finally:
        imu.set_accel(odr_hz=saved_odr)
        imu.set_gyro(odr_hz=saved_odr)


def test_rate_fifo(imu, rpt, duration_s=5, watermark=200):
    """The real rate test: what can the SENSOR sustain into the FIFO?

    Success criterion is zero overruns. An overrun means words were produced
    faster than they were drained and data was lost -- which is the ceiling
    this whole architecture exists to find.
    """
    rpt.section("FIFO rate sweep")
    saved_odr = imu.odr_hz
    buf = bytearray(watermark * L.WORD_LEN)

    try:
        for odr in (104, 416, 833, 1666, 3332, 6664):
            imu.set_accel(odr_hz=odr)
            imu.set_gyro(odr_hz=odr)
            imu.fifo_configure(watermark_words=watermark, bdr_hz=odr,
                               timestamp_decim=1)
            time.sleep_ms(100)

            words = 0
            drains = 0
            overran = False
            elapsed = _common.stopwatch()
            budget_us = duration_s * 1_000_000

            while elapsed() < budget_us:
                level, wtm, ovr, _full = imu.fifo_status()
                if ovr:
                    overran = True
                if level >= watermark:
                    imu.fifo_read_into(buf, watermark)
                    words += watermark
                    drains += 1

            secs = elapsed() / 1_000_000.0
            word_rate = words / secs

            # accel + gyro both batched = 2 words per sample instant, plus a
            # timestamp word per batch.
            sample_rate = word_rate / 2.0

            rpt.measure("fifo_words_per_s_at_odr%d" % odr, word_rate, "words/s")
            rpt.measure("fifo_samples_per_s_at_odr%d" % odr, sample_rate, "Hz")
            rpt.check("no FIFO overrun at ODR %d" % odr, not overran,
                      "%d words in %d drains" % (words, drains))

            if overran:
                rpt.info("  Overran: the drain could not keep up. This is the "
                         "ceiling. Raise the watermark or lower the ODR.")
            imu.fifo_bypass()
    finally:
        imu.fifo_bypass()
        imu.set_accel(odr_hz=saved_odr)
        imu.set_gyro(odr_hz=saved_odr)


def test_fifo_tags(imu, rpt, watermark=64):
    """Decode one drain and show the tag distribution.

    Confirms the FIFO is producing what was asked for -- accel, gyro and
    timestamp words in the expected proportion -- before trusting a long run.
    """
    rpt.section("FIFO tag distribution")
    buf = bytearray(watermark * L.WORD_LEN)

    imu.fifo_configure(watermark_words=watermark, timestamp_decim=1)
    time.sleep_ms(200)

    deadline = time.ticks_add(time.ticks_ms(), 2000)
    while imu.fifo_level() < watermark:
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            rpt.check("FIFO filled to watermark", False, "timed out")
            imu.fifo_bypass()
            return
        time.sleep_ms(5)

    imu.fifo_read_into(buf, watermark)
    imu.fifo_bypass()

    counts = {}
    for i in range(watermark):
        word = buf[i * L.WORD_LEN:(i + 1) * L.WORD_LEN]
        tag, _cnt, _x, _y, _z = L.LSM6DSO32.decode_word(word)
        counts[tag] = counts.get(tag, 0) + 1

    for tag, n in sorted(counts.items()):
        rpt.info("tag 0x%02X %-12s %d words" % (tag, L.TAG_NAMES.get(tag, "?"), n))

    rpt.check("accel words present", counts.get(L.TAG_ACCEL, 0) > 0)
    rpt.check("gyro words present", counts.get(L.TAG_GYRO, 0) > 0)
    rpt.check("timestamp words present", counts.get(L.TAG_TIMESTAMP, 0) > 0,
              "these are what anchor samples to the sensor clock")


# ------------------------------------------------------------ power / reset
def test_power_modes(imu, rpt):
    """Accel and gyro power down independently. Measure current externally."""
    rpt.section("Power modes and runtime reconfiguration")

    rpt.info("Put a meter in series with the board to record current for each "
             "state; MicroPython cannot read it (no sense divider on VBAT).")

    imu.set_accel(odr_hz=config.IMU_ODR_HZ)
    imu.set_gyro(odr_hz=config.IMU_ODR_HZ)
    time.sleep_ms(200)
    rpt.info("both on   -- record current now")
    time.sleep(2)

    imu.power_down_gyro()
    time.sleep_ms(200)
    rpt.info("gyro off  -- record current now")
    time.sleep(2)
    # STATUS may still advertise the final unread sample produced before the
    # ODR was set to zero. Consume it, then confirm no replacement arrives.
    imu.read()
    time.sleep_ms(50)
    _xl_ready, gyro_ready, _temp_ready = imu.data_ready()
    rpt.check("gyro stops producing data when powered down", not gyro_ready,
              "output registers retain the last sample by design")

    imu.power_down_accel()
    time.sleep_ms(200)
    rpt.info("both off  -- record current now")
    time.sleep(2)

    imu.set_accel(odr_hz=config.IMU_ODR_HZ)
    imu.set_gyro(odr_hz=config.IMU_ODR_HZ)
    time.sleep_ms(200)

    # How many samples are garbage across a live full-scale change?
    imu.set_accel(fs_g=4)
    time.sleep_ms(50)
    before = [imu.read()[2] for _ in range(10)]
    imu.set_accel(fs_g=32)
    after = [imu.read()[2] for _ in range(10)]
    after_stable = after[3:]
    rpt.info("az at +/-4g:  %s" % " ".join("%.3f" % v for v in before[:5]))
    rpt.info("az at +/-32g: %s" % " ".join("%.3f" % v for v in after[:5]))
    rpt.info("The first samples after a range change can use the old range's "
             "counts with the new scale; flight code must discard them.")
    rpt.check("full-scale change settles without reset",
              abs(_common.mean(after_stable) - _common.mean(before)) < 0.2,
              "compared after discarding three transition samples")


def test_reset(imu, rpt):
    rpt.section("Reset")

    elapsed = _common.stopwatch()
    ok = imu.reset()
    t_reset = elapsed() / 1000.0
    rpt.check("SW_RESET completes and self-clears", ok, "%.1f ms" % t_reset)
    rpt.measure("sw_reset_ms", t_reset, "ms")

    # After reset the part is in power-down; WHO_AM_I should still answer.
    rpt.check("WHO_AM_I still valid after reset",
              imu.whoami() == L.EXPECTED_WHOAMI)

    imu.init()
    time.sleep_ms(100)
    elapsed = _common.stopwatch()
    while True:
        xl, g, _t = imu.data_ready()
        if xl and g:
            break
        if elapsed() > 500_000:
            break
    rpt.measure("time_to_valid_data_ms", elapsed() / 1000.0, "ms")
    rpt.check("data flows again after re-init", imu.data_ready()[0])


# ------------------------------------------------------------- calibration
def test_gyro_bias(imu, rpt, samples=2000, settle_s=5):
    """Zero-rate offset. THE number that has to be re-measured every boot.

    Run this, power cycle, run it again -- five times. If the spread across
    power cycles is comparable to the bias itself, storing a single calibration
    is worthless and it must be measured at every boot.
    """
    rpt.section("Gyro zero-rate offset")
    _common.countdown(settle_s, "Put the board down and leave it still.")

    bias = imu.measure_gyro_bias(samples=samples, delay_ms=1)
    rpt.measure("gyro_bias_x_dps", bias[0], "dps")
    rpt.measure("gyro_bias_y_dps", bias[1], "dps")
    rpt.measure("gyro_bias_z_dps", bias[2], "dps")
    rpt.measure("die_temp_at_bias_c", imu.read_temp(), "degC")

    worst = max(abs(b) for b in bias)
    rpt.check("bias under 5 dps", worst < 5.0, "worst axis %.3f dps" % worst)
    rpt.info("Record this, POWER CYCLE, and run again. Five times. The spread "
             "across power cycles is the result that matters, not any single "
             "reading.")


def test_gyro_drift(imu, rpt, minutes=30, interval_s=10):
    """Log gyro bias against die temperature from cold.

    Answers whether a warm-up delay is needed before arming, or whether a
    temperature-compensation curve is worth fitting.
    """
    rpt.section("Gyro bias vs temperature (%d min)" % minutes)
    _common.countdown(5, "Board still, starting from cold. Do not touch it.")

    print("  %8s %8s %10s %10s %10s" % ("min", "degC", "bias_x", "bias_y", "bias_z"))
    samples = []
    t_end = time.time() + minutes * 60
    t_start = time.time()

    while time.time() < t_end:
        temp = imu.read_temp()
        bias = imu.measure_gyro_bias(samples=200, delay_ms=0)
        mins = (time.time() - t_start) / 60.0
        print("  %8.1f %8.2f %10.4f %10.4f %10.4f"
              % (mins, temp, bias[0], bias[1], bias[2]))
        samples.append((mins, temp, bias))
        time.sleep(interval_s)

    if len(samples) >= 2:
        t0, temp0, b0 = samples[0]
        t1, temp1, b1 = samples[-1]
        rpt.measure("temp_rise_c", temp1 - temp0, "degC")
        for i, axis in enumerate("xyz"):
            drift = b1[i] - b0[i]
            rpt.measure("gyro_drift_%s_dps" % axis, drift, "dps")
            if temp1 != temp0:
                rpt.measure("gyro_tempco_%s_dps_per_c" % axis,
                            drift / (temp1 - temp0), "dps/degC")
    return samples


def test_accel_tumble(imu, rpt):
    """Six-position calibration: per-axis bias and scale.

    Interactive. Each face gets pointed down in turn, so each axis sees +1g
    and -1g. bias = (high + low)/2, scale = (high - low)/2.
    """
    rpt.section("Accel six-position tumble")

    positions = (
        ("+X down", 0, +1), ("-X down", 0, -1),
        ("+Y down", 1, +1), ("-Y down", 1, -1),
        ("+Z down", 2, +1), ("-Z down", 2, -1),
    )
    readings = {}

    for label, axis, sign in positions:
        try:
            input("\n  Place the board %s and press Enter (Ctrl-C to abort): "
                  % label)
        except (EOFError, KeyboardInterrupt):
            rpt.skip("accel tumble", "aborted by operator")
            return None
        time.sleep(1)
        vals = []
        for _ in range(100):
            a = imu.read()[:3]
            vals.append(a[axis])
            time.sleep_ms(5)
        m = _common.mean(vals)
        readings[(axis, sign)] = m
        print("      axis %s measured %+.4f g (expected %+d)"
              % ("xyz"[axis], m, sign))

    bias = []
    scale = []
    for axis in range(3):
        hi = readings.get((axis, +1))
        lo = readings.get((axis, -1))
        if hi is None or lo is None:
            continue
        b = (hi + lo) / 2.0
        s = (hi - lo) / 2.0
        bias.append(b)
        scale.append(s)
        rpt.measure("accel_bias_%s_g" % "xyz"[axis], b, "g")
        rpt.measure("accel_scale_%s" % "xyz"[axis], s, "(1.0 is ideal)")

    if len(scale) == 3:
        rpt.check("all axis scales within 10% of 1.0",
                  all(0.9 < s < 1.1 for s in scale),
                  "scales %.4f %.4f %.4f" % tuple(scale))
        rpt.info("Accel bias is usually stable across power cycles -- unlike "
                 "gyro bias. Repeat once after a power cycle to confirm, then "
                 "it can be stored rather than re-measured every boot.")
    return (bias, scale)


# ------------------------------------------------------------------- runner
def run_all(skip_slow=True, skip_interactive=True):
    rpt = _common.Report("LSM6DSO32 characterization")
    _common.mem_report(rpt)

    imu = setup(rpt)

    test_identity(imu, rpt)
    test_channels(imu, rpt)
    test_scale_factors(imu, rpt)
    test_self_test(imu, rpt)
    test_fifo_tags(imu, rpt)
    test_rate_polled(imu, rpt)
    test_rate_fifo(imu, rpt)
    test_power_modes(imu, rpt)
    test_reset(imu, rpt)
    test_gyro_bias(imu, rpt)

    if skip_slow:
        rpt.skip("gyro bias vs temperature",
                 "30 min run -- call test_gyro_drift(imu, rpt) directly")
    else:
        test_gyro_drift(imu, rpt)

    if skip_interactive:
        rpt.skip("accel six-position tumble",
                 "interactive -- call test_accel_tumble(imu, rpt) directly")
    else:
        test_accel_tumble(imu, rpt)

    rpt.summary()
    return imu, rpt
