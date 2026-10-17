# bench/test_baro.py -- BME280 / BMP280 characterization.
#
#     from bench import test_baro; test_baro.run_all()
#
# The headline question is not "how fast does it go" -- it is the trade
# between pressure noise, conversion time and filter lag. Altitude resolution
# comes from oversampling, but oversampling costs rate, and the IIR filter
# that quiets the signal also makes it lag. A rocket climbing at 200 m/s is
# badly served by a filter tuned for a weather station.

import time

import config
from bench import _common
from drivers import bme280 as B


def setup(rpt=None):
    i2c = _common.setup_bus(config.BARO_BUS)
    baro = B.BME280(i2c, addr=config.BARO_ADDR)
    baro.init()
    return baro


def test_identity(baro, rpt):
    rpt.section("Identity")
    cid = baro.chip_id
    rpt.measure("chip_id", "0x%02X" % cid)
    rpt.check("chip id is 0x60 or 0x58", cid in (B.ID_BME280, B.ID_BMP280),
              "got 0x%02X" % cid)
    if cid == B.ID_BME280:
        rpt.info("BME280 -- has humidity.")
    elif cid == B.ID_BMP280:
        rpt.warn("BMP280, not BME280. No humidity channel. The spec assumed "
                 "humidity would be available; it is not.")
    rpt.check("calibration coefficients loaded", len(baro._cal) >= 12,
              "%d coefficients" % len(baro._cal))
    rpt.info("These are per-chip factory values and travel in the log header. "
             "A log decoded against another chip's coefficients is wrong in a "
             "way that looks entirely plausible.")


def test_channels(baro, rpt):
    rpt.section("Channels and conversion")

    # Immediately after reset the data registers contain the 0x80000
    # power-on sentinel. Wait for the first real normal-mode conversion.
    deadline = time.ticks_add(time.ticks_ms(), 500)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if baro.read_raw()[0] != 0x80000:
            break
        time.sleep_ms(5)

    adc_p, adc_t, adc_h = baro.read_raw()
    rpt.info("raw   adc_p=%d  adc_t=%d  adc_h=%d" % (adc_p, adc_t, adc_h))

    t, p, h = baro.read_compensated()
    rpt.measure("temp_c", t, "degC")
    rpt.measure("pressure_pa", p, "Pa")
    if h is not None:
        rpt.measure("humidity_pct", h, "%")

    rpt.check("temperature plausible", -40.0 < t < 85.0, "%.2f degC" % t)
    # 300-1100 hPa is the part's full range; sea level is ~1013 hPa.
    rpt.check("pressure in sensor range", 30000.0 < p < 110000.0,
              "%.1f Pa (%.2f hPa)" % (p, p / 100.0))
    if h is not None:
        rpt.check("humidity in range", 0.0 <= h <= 100.0, "%.1f %%" % h)


def test_rate_vs_oversampling(baro, rpt, samples=100):
    """Measured rate for each oversampling combination.

    The datasheet's 157Hz applies only at minimum oversampling. The settings
    that altitude actually wants land far lower, and this is the table that
    proves it rather than assuming it.
    """
    rpt.section("Rate vs oversampling")
    saved = (baro.osr_t, baro.osr_p, baro.osr_h, baro.iir, baro.standby)

    combos = (
        (1, 1, 1, "lowest"),
        (1, 2, 1, "p x2"),
        (2, 3, 1, "p x4"),
        (2, 4, 1, "p x8"),
        (2, 5, 1, "p x16 (altitude setting)"),
        (5, 5, 5, "everything x16"),
    )

    print("  %-26s %10s %10s %10s" % ("config", "predicted", "measured", "ratio"))
    try:
        for osr_t, osr_p, osr_h, label in combos:
            baro.configure(osr_t=osr_t, osr_p=osr_p, osr_h=osr_h,
                           standby=0, mode=B.MODE_NORMAL)
            time.sleep_ms(200)

            predicted = baro.expected_rate_hz()

            # Count distinct readings, not read calls: polling faster than the
            # conversion just returns the same value again, which would report
            # a fictitious rate.
            last = None
            changes = 0
            elapsed = _common.stopwatch()
            budget_us = 3_000_000
            while elapsed() < budget_us:
                cur = baro.read_raw()[0]
                if last is not None and cur != last:
                    changes += 1
                last = cur
            measured = changes / (elapsed() / 1_000_000.0)

            print("  %-26s %10.1f %10.1f %10.2f"
                  % (label, predicted, measured, measured / predicted if predicted else 0))
            rpt.measure("baro_hz_t%d_p%d_h%d" % (osr_t, osr_p, osr_h),
                        measured, "Hz")
    finally:
        baro.configure(osr_t=saved[0], osr_p=saved[1], osr_h=saved[2],
                       iir=saved[3], standby=saved[4], mode=B.MODE_NORMAL)


def test_noise(baro, rpt, seconds=30):
    """Pressure RMS noise per oversampling setting, converted to metres.

    This is the altitude-resolution-vs-rate curve. It is the single most
    useful table this script produces, because it is what decides the flight
    configuration.
    """
    rpt.section("Pressure noise -> altitude resolution")
    rpt.info("Board still, no draughts, no doors opening. %ds per setting."
             % seconds)
    saved = (baro.osr_t, baro.osr_p, baro.osr_h, baro.iir)

    print("  %-14s %12s %12s %10s" % ("oversample", "RMS noise Pa", "res. metres", "rate Hz"))
    try:
        for osr_p in (1, 2, 3, 4, 5):
            baro.configure(osr_t=2, osr_p=osr_p, osr_h=1, iir=0,
                           standby=0, mode=B.MODE_NORMAL)
            time.sleep_ms(300)

            readings = []
            deadline = time.ticks_add(time.ticks_ms(), seconds * 1000)
            last = None
            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                adc_p, adc_t, _h = baro.read_raw()
                if adc_p != last:
                    t_c, t_fine = B.compensate_t(adc_t, baro._cal)
                    readings.append(B.compensate_p(adc_p, t_fine, baro._cal))
                    last = adc_p

            if len(readings) < 10:
                rpt.warn("only %d readings at osr_p=%d" % (len(readings), osr_p))
                continue

            noise_pa = _common.stdev(readings)
            res_m = noise_pa * B.METRES_PER_PA
            rate = len(readings) / float(seconds)

            print("  p x%-12d %12.3f %12.3f %10.1f"
                  % (B.OSR_MULT[osr_p], noise_pa, res_m, rate))
            rpt.measure("baro_noise_pa_osr%d" % osr_p, noise_pa, "Pa")
            rpt.measure("baro_res_m_osr%d" % osr_p, res_m, "m")
    finally:
        baro.configure(osr_t=saved[0], osr_p=saved[1], osr_h=saved[2],
                       iir=saved[3], mode=B.MODE_NORMAL)


def test_iir_response(baro, rpt, seconds=20):
    """IIR filter lag. Needs a physical pressure step.

    Carry the board up a stairwell, or seal it in a syringe and push. The
    filter's settling time is the cost of its noise reduction, and for a
    vehicle climbing at 200 m/s that lag is a real altitude error.
    """
    rpt.section("IIR filter step response")
    saved_iir = baro.iir

    print("\n  For each setting: hold still, then make a fast pressure change")
    print("  (up a stairwell, or a sealed syringe) and hold the new level.\n")

    try:
        for iir in (0, 1, 2, 3, 4):
            baro.configure(osr_t=2, osr_p=5, osr_h=1, iir=iir,
                           standby=0, mode=B.MODE_NORMAL)
            time.sleep_ms(300)
            coeff = B.IIR_COEFF[iir]

            try:
                input("  IIR x%-2d -- press Enter, then make the step: " % coeff)
            except (EOFError, KeyboardInterrupt):
                rpt.skip("IIR step response", "aborted by operator")
                return

            series = []
            t0 = time.ticks_ms()
            deadline = time.ticks_add(t0, seconds * 1000)
            last = None
            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                adc_p, adc_t, _h = baro.read_raw()
                if adc_p != last:
                    t_c, t_fine = B.compensate_t(adc_t, baro._cal)
                    series.append((time.ticks_diff(time.ticks_ms(), t0),
                                   B.compensate_p(adc_p, t_fine, baro._cal)))
                    last = adc_p

            if len(series) < 20:
                rpt.warn("too few samples at IIR x%d" % coeff)
                continue

            start_p = _common.mean([p for _t, p in series[:10]])
            end_p = _common.mean([p for _t, p in series[-10:]])
            span = end_p - start_p

            if abs(span) < 5.0:
                rpt.warn("IIR x%d: pressure barely moved (%.1f Pa). No step "
                         "detected -- make a bigger change." % (coeff, span))
                continue

            # Time to cross 63% of the step, measured from when it began.
            target = start_p + 0.63 * span
            t_cross = None
            t_begin = None
            for t_ms, p in series:
                if t_begin is None and abs(p - start_p) > abs(span) * 0.1:
                    t_begin = t_ms
                if t_begin is not None:
                    if (span > 0 and p >= target) or (span < 0 and p <= target):
                        t_cross = t_ms - t_begin
                        break

            noise = _common.stdev([p for _t, p in series[:10]])
            rpt.measure("iir_x%d_noise_pa" % coeff, noise, "Pa")
            if t_cross is not None:
                rpt.measure("iir_x%d_tau_ms" % coeff, t_cross, "ms")
                # 200 m/s * tau = altitude error contributed by filter lag.
                rpt.measure("iir_x%d_lag_at_200mps_m" % coeff,
                            200.0 * t_cross / 1000.0, "m")
            else:
                rpt.warn("IIR x%d: could not find 63%% crossing" % coeff)
    finally:
        baro.configure(iir=saved_iir, mode=B.MODE_NORMAL)
        rpt.info("Pick the knee: the largest IIR whose lag at 200 m/s is still "
                 "smaller than the altitude error you are willing to accept.")


def test_modes(baro, rpt):
    rpt.section("Modes")

    baro.sleep()
    time.sleep_ms(100)
    a = baro.read_raw()[0]
    time.sleep_ms(300)
    b = baro.read_raw()[0]
    rpt.check("sleep mode stops conversions", a == b,
              "adc_p %d then %d" % (a, b))
    rpt.info("Record current in sleep mode with an external meter.")

    elapsed = _common.stopwatch()
    ok = baro.force_once()
    t_forced = elapsed() / 1000.0
    rpt.check("forced mode completes one conversion", ok, "%.1f ms" % t_forced)
    rpt.measure("forced_conversion_ms", t_forced, "ms")
    predicted = B.measurement_time_ms(baro.osr_t, baro.osr_p, baro.osr_h)
    rpt.info("predicted %.1f ms from the timing model" % predicted)

    baro.configure(mode=B.MODE_NORMAL)
    time.sleep_ms(200)
    a = baro.read_raw()[0]
    time.sleep_ms(300)
    b = baro.read_raw()[0]
    rpt.check("normal mode runs continuously", a != b,
              "adc_p changed from %d to %d" % (a, b))


def test_reset(baro, rpt):
    rpt.section("Reset")
    cal_before = dict(baro._cal)

    baro.reset()
    time.sleep_ms(50)
    rpt.check("chip id still readable after reset",
              baro.read_id() == baro.chip_id)

    baro._load_calibration()
    rpt.check("calibration coefficients survive reset",
              baro._cal == cal_before,
              "they live in NVM but must be re-read after reset")

    baro.configure()
    time.sleep_ms(200)
    rpt.check("conversions resume after re-configure",
              baro.read_raw()[0] > 0)


def test_altitude_reference(baro, rpt, samples=50):
    """AGL altitude against a pad reference.

    Absolute altitude needs a sea-level pressure this board cannot know.
    Height above the launch point is both computable and what the mission
    wants.
    """
    rpt.section("Altitude reference")

    readings = []
    for _ in range(samples):
        t, p, _h = baro.read_compensated()
        readings.append(p)
        time.sleep_ms(20)
    pad = _common.mean(readings)
    rpt.measure("pad_pressure_pa", pad, "Pa")

    zero = B.altitude_agl_m(pad, pad)
    rpt.check("altitude at the pad reference is 0", abs(zero) < 0.01,
              "%.4f m" % zero)

    for delta_pa in (100.0, 1000.0):
        h = B.altitude_agl_m(pad - delta_pa, pad)
        rpt.info("%.0f Pa drop -> %.2f m AGL" % (delta_pa, h))

    noise = _common.stdev(readings)
    rpt.measure("pad_pressure_noise_pa", noise, "Pa")
    rpt.measure("pad_altitude_noise_m", noise * B.METRES_PER_PA, "m")
    rpt.info("Verify against a known height -- one storey is about 3 m and "
             "should show up as roughly 36 Pa.")


def run_all(skip_interactive=True):
    rpt = _common.Report("BME280 characterization")
    _common.mem_report(rpt)

    baro = setup(rpt)

    test_identity(baro, rpt)
    test_channels(baro, rpt)
    test_modes(baro, rpt)
    test_rate_vs_oversampling(baro, rpt)
    test_noise(baro, rpt)
    test_reset(baro, rpt)
    test_altitude_reference(baro, rpt)

    if skip_interactive:
        rpt.skip("IIR step response",
                 "interactive -- call test_iir_response(baro, rpt) directly")
    else:
        test_iir_response(baro, rpt)

    rpt.summary()
    return baro, rpt
