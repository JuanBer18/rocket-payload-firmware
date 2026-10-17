# bench/_common.py -- shared scaffolding for the characterization scripts.
#
# Every bench script prints a machine-readable-ish pass/fail line per check
# and a summary at the end, so results can be pasted straight into RESULTS.md
# without retyping numbers by hand.

import gc
import time

import config


class Report:
    """Collects check results and prints them as they happen."""

    def __init__(self, title):
        self.title = title
        self.checks = []
        self.measurements = {}
        print("\n" + "=" * 62)
        print(title)
        print("=" * 62)

    def section(self, name):
        print("\n--- %s" % name)

    def check(self, name, passed, detail=""):
        self.checks.append((name, bool(passed), detail))
        print("  [%s] %s%s" % ("PASS" if passed else "FAIL", name,
                               ("  %s" % detail) if detail else ""))
        return passed

    def info(self, text):
        print("       %s" % text)

    def warn(self, text):
        print("  [WARN] %s" % text)

    def skip(self, name, why):
        self.checks.append((name, None, why))
        print("  [SKIP] %s  %s" % (name, why))

    def measure(self, key, value, unit=""):
        """Record a number destined for RESULTS.md."""
        self.measurements[key] = (value, unit)
        if isinstance(value, float):
            print("       %-32s %10.4f %s" % (key, value, unit))
        else:
            print("       %-32s %10s %s" % (key, value, unit))

    def summary(self):
        passed = sum(1 for _n, p, _d in self.checks if p is True)
        failed = sum(1 for _n, p, _d in self.checks if p is False)
        skipped = sum(1 for _n, p, _d in self.checks if p is None)
        print("\n" + "-" * 62)
        print("%s: %d passed, %d failed, %d skipped"
              % (self.title, passed, failed, skipped))
        if failed:
            print("\nFailed checks:")
            for name, p, detail in self.checks:
                if p is False:
                    print("  - %s  %s" % (name, detail))
        if self.measurements:
            print("\nFor RESULTS.md:")
            for key, (value, unit) in self.measurements.items():
                if isinstance(value, float):
                    print("  %-34s %.4f %s" % (key, value, unit))
                else:
                    print("  %-34s %s %s" % (key, value, unit))
        print("-" * 62)
        return failed == 0


def setup_bus(bus_num=0, freq=None):
    """Bring up I2C and show what answered.

    Imported lazily so this module stays importable on a desktop for syntax
    checking, where `machine` does not exist.
    """
    from core import buses
    i2c = buses.init_bus(bus_num, freq)
    default_freq = config.I2C0_FREQ if bus_num == 0 else config.I2C1_FREQ
    print("I2C%d @ %d Hz on SDA=%s SCL=%s"
          % (bus_num, freq or default_freq,
             config.I2C0_SDA if bus_num == 0 else config.I2C1_SDA,
             config.I2C0_SCL if bus_num == 0 else config.I2C1_SCL))
    print(buses.describe_scan(i2c, bus_num))
    return i2c


def mem_report(report):
    gc.collect()
    free = gc.mem_free()
    total = free + gc.mem_alloc()
    report.measure("gc.mem_free", free, "bytes")
    report.measure("gc.heap_total", total, "bytes")
    # The N16R2 has 2MB quad PSRAM. ~223KB means the octal firmware got
    # flashed and PSRAM never initialised -- worth catching early because
    # everything still runs, just with a fraction of the heap.
    if total < config.EXPECTED_MEM_FREE_MIN:
        report.warn("total heap %d < %d expected. Wrong firmware? This board "
                    "needs the plain ESP32_GENERIC_S3 (quad PSRAM) build, "
                    "not SPIRAM_OCT." % (total, config.EXPECTED_MEM_FREE_MIN))
    return free


def stopwatch():
    """-> a function returning microseconds since it was created."""
    t0 = time.ticks_us()

    def elapsed():
        return time.ticks_diff(time.ticks_us(), t0)
    return elapsed


def mean(values):
    return sum(values) / len(values) if values else 0.0


def stdev(values):
    """Population standard deviation. No statistics module in MicroPython."""
    n = len(values)
    if n < 2:
        return 0.0
    m = mean(values)
    return (sum((v - m) ** 2 for v in values) / n) ** 0.5


def countdown(seconds, message):
    """Give the operator time to put the board down before a still-measurement."""
    print("\n%s" % message)
    for i in range(seconds, 0, -1):
        print("  starting in %d..." % i)
        time.sleep(1)
    print("  measuring, do not touch the board")
