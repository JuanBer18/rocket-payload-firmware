# bench/test_sd.py -- SD card and binary logging characterization.
#
#     from bench import test_sd; test_sd.run_all()
#
# The number this is really after is sustained write throughput, because that
# is what caps the logging rate. A 1.6kHz IMU produces ~23KB/s of FIFO data;
# if the card cannot take that continuously, no amount of clever FIFO
# handling helps.

import os
import time

import config
import recfmt
from bench import _common
from core import storage


def setup(rpt=None):
    if not storage.is_mounted():
        storage.mount()
    print("SD mounted at %s, %.1f MB free"
          % (config.SD_MOUNT, storage.free_bytes() / 1e6))
    return config.SD_MOUNT


def test_mount(rpt):
    rpt.section("Mount")
    try:
        storage.unmount()
        elapsed = _common.stopwatch()
        storage.mount()
        rpt.check("mounts on SPI slot %d" % config.SD_SPI_SLOT, True,
                  "%.0f ms" % (elapsed() / 1000.0))
    except storage.SDMountError as e:
        rpt.check("mounts on SPI slot %d" % config.SD_SPI_SLOT, False, str(e))
        return False

    free = storage.free_bytes()
    rpt.measure("sd_free_mb", free / 1e6, "MB")
    rpt.check("card has free space", free > 1_000_000)

    existing = storage.list_logs()
    rpt.info("%d existing log file(s)" % len(existing))
    for name, size in existing[-5:]:
        rpt.info("  %s  %.2f MB" % (name, size / 1e6))
    return True


def test_throughput(rpt, seconds=10):
    """Sustained write rate by block size.

    Small blocks lose to per-call overhead; very large blocks stop helping
    once the card's own buffer is the limit. The knee tells you what FIFO
    watermark to configure.
    """
    rpt.section("Write throughput by block size")
    print("  %10s %12s %12s" % ("block", "MB/s", "writes/s"))

    best = (0, 0.0)
    for block in (512, 4096, 16384, 65536):
        data = bytes(block)
        path = "%s/_tp_test.bin" % config.SD_MOUNT
        n = 0
        try:
            with open(path, "wb") as f:
                elapsed = _common.stopwatch()
                budget = seconds * 1_000_000
                while elapsed() < budget:
                    f.write(data)
                    n += 1
                f.flush()
                secs = elapsed() / 1_000_000.0
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        mbps = (n * block) / secs / 1e6
        print("  %10d %12.3f %12.1f" % (block, mbps, n / secs))
        rpt.measure("sd_write_mbps_%d" % block, mbps, "MB/s")
        if mbps > best[1]:
            best = (block, mbps)

    rpt.measure("sd_best_block_bytes", best[0], "bytes")
    rpt.measure("sd_best_throughput_mbps", best[1], "MB/s")

    # Translate the result into the thing it actually constrains.
    imu_bytes_per_sample = 2 * recfmt.IMU_FIFO_WORD_LEN   # accel + gyro words
    max_hz = best[1] * 1e6 / imu_bytes_per_sample
    rpt.measure("implied_max_imu_hz", max_hz, "Hz")
    rpt.info("At %.2f MB/s and %d bytes per accel+gyro sample, the card caps "
             "IMU logging near %.0f Hz before anything else does."
             % (best[1], imu_bytes_per_sample, max_hz))
    rpt.check("card can sustain 1.6 kHz IMU logging", max_hz > 1666,
              "%.0f Hz headroom" % max_hz)


def test_flush_cost(rpt, seconds=5, block=4096):
    """What durability costs.

    Flushing every write is safest and slowest. This quantifies the trade so
    LOG_FLUSH_EVERY is a decision rather than a guess.
    """
    rpt.section("Flush cadence vs throughput")
    print("  %14s %12s" % ("flush every", "MB/s"))

    data = bytes(block)
    for every in (1, 10, 100, 0):
        path = "%s/_flush_test.bin" % config.SD_MOUNT
        n = 0
        try:
            with open(path, "wb") as f:
                elapsed = _common.stopwatch()
                budget = seconds * 1_000_000
                while elapsed() < budget:
                    f.write(data)
                    n += 1
                    if every and n % every == 0:
                        f.flush()
                f.flush()
                secs = elapsed() / 1_000_000.0
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        mbps = (n * block) / secs / 1e6
        label = "never" if every == 0 else str(every)
        print("  %14s %12.3f" % (label, mbps))
        rpt.measure("sd_mbps_flush_%s" % label, mbps, "MB/s")

    rpt.info("Pick LOG_FLUSH_EVERY from this plus the power-loss result: the "
             "flush interval bounds how much data a sudden power cut costs.")


def test_log_numbering(rpt):
    rpt.section("Log file numbering")
    from core import logger

    path = logger.next_log_path()
    rpt.info("next log would be %s" % path)
    rpt.check("next path does not already exist",
              path.split("/")[-1] not in [n for n, _s in storage.list_logs()])

    # Create it, confirm the next call steps past it, then clean up.
    with open(path, "wb") as f:
        f.write(b"x")
    second = logger.next_log_path()
    rpt.check("numbering advances past an existing file", second != path,
              "%s then %s" % (path.split("/")[-1], second.split("/")[-1]))
    try:
        os.remove(path)
    except OSError:
        pass


def test_binary_roundtrip(rpt):
    """Write a real log with the real logger, read it back, check the framing.

    Catches format bugs on-board rather than after a flight.
    """
    rpt.section("Binary log round-trip")
    from core import clock, logger

    path = "%s/_fmt_test.bin" % config.SD_MOUNT
    lg = logger.BinaryLogger(path, clock.MONO, flush_every=1)
    lg.open({"test": True, "note": "bench/test_sd.py round-trip"})

    fifo_words = 32
    buf = bytearray(fifo_words * recfmt.IMU_FIFO_WORD_LEN)
    for i in range(len(buf)):
        buf[i] = i & 0xFF

    lg.write_marker(clock.us(), recfmt.MARK_TEST, "start")
    lg.write_imu_fifo(clock.us(), buf, fifo_words)
    lg.write_baro(clock.us(), 123456, 654321, 30000)
    lg.write_sen65(clock.us(), (1, 2, 3, 4, 5, 6, 7, 8), 0)
    lg.write_timesync(clock.us(), 0xDEADBEEF)
    lg.write_marker(clock.us(), recfmt.MARK_TEST, "end")
    stats = lg.stats()
    lg.close()

    size = os.stat(path)[6]
    rpt.measure("roundtrip_records", stats["records"])
    rpt.measure("roundtrip_bytes", size, "bytes")
    rpt.check("file size matches bytes written", size == stats["bytes"],
              "%d on card, %d counted" % (size, stats["bytes"]))

    # Walk the framing back.
    import struct
    with open(path, "rb") as f:
        raw = f.read()

    magic, version, jlen = struct.unpack_from(recfmt.FILE_HDR_FMT, raw, 0)
    rpt.check("file magic", magic == recfmt.MAGIC, repr(magic))
    rpt.check("format version", version == recfmt.FORMAT_VERSION, str(version))

    off = recfmt.FILE_HDR_LEN + jlen
    seen = {}
    while off < len(raw):
        sync, rtype, plen = struct.unpack_from(recfmt.REC_HEADER_FMT, raw, off)
        if sync != recfmt.SYNC:
            rpt.check("record framing intact", False, "bad sync at %d" % off)
            break
        seen[rtype] = seen.get(rtype, 0) + 1
        off += recfmt.REC_HEADER_LEN + plen
    else:
        rpt.check("record framing intact", True,
                  "%d record types: %s"
                  % (len(seen), ", ".join(recfmt.REC_NAMES.get(t, str(t))
                                          for t in sorted(seen))))

    rpt.check("all six records present", sum(seen.values()) == 6,
              "found %d" % sum(seen.values()))
    rpt.check("consumed exactly to end of file", off == len(raw),
              "stopped at %d of %d" % (off, len(raw)))

    try:
        os.remove(path)
    except OSError:
        pass


def test_power_loss(rpt, block=4096):
    """Write continuously until power is yanked.

    Run this, pull power mid-write, reboot, then call
    verify_power_loss() to see how much survived. Repeat five times -- the
    spread is what justifies the flush interval.

    This is also what proves the typed record stream beats CSV: a truncated
    record is detectable and skippable, a truncated text row is ambiguous.
    """
    rpt.section("Power loss survival")
    path = "%s/_powerloss.bin" % config.SD_MOUNT

    print("\n  Writing continuously to %s" % path)
    print("  PULL POWER at any point. Reboot, then run:")
    print("      from bench import test_sd; test_sd.verify_power_loss()")
    print("  Ctrl-C to stop cleanly instead.\n")

    data = bytes(block)
    n = 0
    try:
        with open(path, "wb") as f:
            while True:
                f.write(data)
                n += 1
                if n % config.LOG_FLUSH_EVERY == 0:
                    f.flush()
                if n % 100 == 0:
                    print("  %d blocks (%.2f MB) written, flushing every %d"
                          % (n, n * block / 1e6, config.LOG_FLUSH_EVERY))
    except KeyboardInterrupt:
        print("\n  stopped cleanly after %d blocks" % n)


def verify_power_loss(block=4096):
    """Call after rebooting from a power-loss test."""
    path = "%s/_powerloss.bin" % config.SD_MOUNT
    if not storage.is_mounted():
        storage.mount()
    try:
        size = os.stat(path)[6]
    except OSError:
        print("No %s found -- was the test run?" % path)
        return None

    blocks = size / float(block)
    print("Survived: %d bytes = %.1f blocks of %d" % (size, blocks, block))
    print("Flush interval was %d blocks." % config.LOG_FLUSH_EVERY)
    print("Whole blocks recovered: %d, partial tail: %d bytes"
          % (int(blocks), size % block))
    print("\nRecord this and repeat. Five runs give the spread that justifies "
          "the flush interval.")
    return size


def run_all(skip_destructive=True):
    rpt = _common.Report("SD card and binary logging")
    _common.mem_report(rpt)

    if not test_mount(rpt):
        rpt.summary()
        return rpt

    test_log_numbering(rpt)
    test_binary_roundtrip(rpt)
    test_throughput(rpt)
    test_flush_cost(rpt)

    if skip_destructive:
        rpt.skip("power loss survival",
                 "needs a physical power yank -- call test_power_loss(rpt)")
    else:
        test_power_loss(rpt)

    rpt.summary()
    return rpt
