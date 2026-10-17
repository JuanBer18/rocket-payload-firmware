# core/clock.py -- wrap-safe monotonic microsecond timebase.
#
# time.ticks_us() wraps every ~17.9 minutes (2^30 us) in MicroPython. Used
# raw as a timestamp it would roll over ~17 times across a 5-hour pad wait and
# destroy cross-sensor alignment, which is the single correctness property
# this whole system exists to provide.
#
# This accumulates ticks_diff() deltas into an arbitrary-precision int, so the
# returned value is monotonic for as long as the board is powered.
#
# CONTRACT: us() must be called at least once every ~8.9 minutes (half the
# wrap period). ticks_diff() can only resolve a delta up to +/-2^29 us; past
# that it aliases and time silently goes backwards. The logger calls it on
# every record and the idle path calls it via tick(), so this holds in normal
# operation -- but a long blocking call with no clock read is a real hazard.
# tick() exists to be called from any wait loop that might otherwise stall.

import time

_WRAP_HALF_US = 1 << 29


class Monotonic:
    def __init__(self):
        self._last = time.ticks_us()
        self._acc = 0

    def us(self):
        """Microseconds since construction. Monotonic across ticks_us wrap."""
        now = time.ticks_us()
        delta = time.ticks_diff(now, self._last)
        # A negative delta here means either the contract above was violated
        # or ticks went backwards. Clamping to 0 keeps the timeline monotonic;
        # the gap is visible in the data as a stall rather than as a jump back
        # in time, which is the failure mode that is actually recoverable.
        if delta < 0:
            delta = 0
        self._acc += delta
        self._last = now
        return self._acc

    def ms(self):
        return self.us() // 1000

    def tick(self):
        """Advance the accumulator without caring about the value.

        Call this from any loop that might go more than a few minutes without
        otherwise reading the clock.
        """
        self.us()

    def elapsed_us(self, since_us):
        return self.us() - since_us


# Process-wide instance. Everything shares one timebase -- two Monotonic
# objects would produce two unrelated timelines and silently break alignment.
MONO = Monotonic()


def us():
    return MONO.us()


def ms():
    return MONO.ms()


def tick():
    MONO.tick()


def sleep_us_ticking(duration_us):
    """Sleep, keeping the accumulator fed.

    A plain sleep longer than the wrap period would break the contract in
    us(). This breaks the wait into chunks and ticks between them.
    """
    remaining = duration_us
    chunk = _WRAP_HALF_US // 2
    while remaining > 0:
        step = chunk if remaining > chunk else remaining
        time.sleep_us(step)
        MONO.tick()
        remaining -= step
