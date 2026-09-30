#!/usr/bin/env python3
"""Rate limiting — the four classic algorithms, from scratch.

A rate limiter answers one question: "has this client made too many requests
too fast?" It's what protects an API from being hammered — by abuse, a runaway
script, or a thundering herd after an outage. Every serious API gateway has one.

There isn't a single right algorithm; there are trade-offs. This module builds
all four common ones so you can see how they differ:

    TokenBucket          - smooth rate with a burst allowance (most popular)
    FixedWindow          - simplest; cheap but allows double bursts at edges
    SlidingWindowLog     - exact, but stores every timestamp (more memory)
    SlidingWindowCounter - the practical approximation (Cloudflare-style)

The key design choice: TIME is injected via a Clock object. Real code uses the
system clock; tests use a FakeClock they can advance by hand. That's what makes
time-based logic testable without real sleeping.

CLI:
    python ratelimit.py demo    # watch all four handle the same burst
"""

import argparse
import time
from collections import defaultdict, deque


# ---------------------------------------------------------------------------
# Clocks — the injectable "what time is it?" dependency.
# ---------------------------------------------------------------------------
class SystemClock:
    """The real clock. Monotonic so it never jumps backward (e.g. NTP resync)."""

    def now(self):
        return time.monotonic()


class FakeClock:
    """A clock the tests control. Advance it manually with .advance(seconds)."""

    def __init__(self, start=0.0):
        self.t = start

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


# ---------------------------------------------------------------------------
# 1) Token Bucket
# ---------------------------------------------------------------------------
class TokenBucket:
    """A bucket holds up to `capacity` tokens and refills at `refill_rate`/sec.

    Each request spends a token. A full bucket lets a client burst up to
    `capacity` requests at once, then settle into the steady refill rate. This
    is the most widely used algorithm because it's smooth AND allows bursts.
    """

    def __init__(self, capacity, refill_rate, clock=None):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.clock = clock or SystemClock()
        # Per client: [tokens_available, last_time_we_refilled].
        self._state = {}

    def allow(self, key="global", cost=1):
        now = self.clock.now()
        tokens, last = self._state.get(key, (self.capacity, now))

        # Add the tokens that have accrued since we last looked.
        tokens = min(self.capacity, tokens + (now - last) * self.refill_rate)

        if tokens >= cost:
            tokens -= cost
            self._state[key] = (tokens, now)
            return True
        self._state[key] = (tokens, now)
        return False


# ---------------------------------------------------------------------------
# 2) Fixed Window
# ---------------------------------------------------------------------------
class FixedWindow:
    """Count requests in fixed clock windows (e.g. each 60-second block).

    Dead simple and cheap — one counter per client. Its famous flaw: a client
    can send `limit` requests at the very end of one window and `limit` more at
    the very start of the next, so up to 2x the limit crosses a window edge.
    """

    def __init__(self, limit, window_seconds, clock=None):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock or SystemClock()
        # Per client: [window_id, count_in_that_window].
        self._state = {}

    def allow(self, key="global"):
        now = self.clock.now()
        window_id = int(now // self.window)
        wid, count = self._state.get(key, (window_id, 0))

        if wid != window_id:  # a new window started — reset the count
            wid, count = window_id, 0

        if count < self.limit:
            self._state[key] = (wid, count + 1)
            return True
        self._state[key] = (wid, count)
        return False


# ---------------------------------------------------------------------------
# 3) Sliding Window Log
# ---------------------------------------------------------------------------
class SlidingWindowLog:
    """Keep a timestamp for every request in the last `window` seconds.

    Perfectly accurate: the limit applies to any rolling window, with no edge
    burst. The cost is memory — it stores one timestamp per recent request.
    """

    def __init__(self, limit, window_seconds, clock=None):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock or SystemClock()
        self._log = defaultdict(deque)  # per client: timestamps of recent hits

    def allow(self, key="global"):
        now = self.clock.now()
        log = self._log[key]

        # Drop timestamps that have slid out of the window.
        cutoff = now - self.window
        while log and log[0] <= cutoff:
            log.popleft()

        if len(log) < self.limit:
            log.append(now)
            return True
        return False


# ---------------------------------------------------------------------------
# 4) Sliding Window Counter
# ---------------------------------------------------------------------------
class SlidingWindowCounter:
    """The practical middle ground: two counters and a weighted estimate.

    It keeps just the current and previous fixed-window counts, then estimates
    the rolling count as:  current + previous * (fraction of the previous window
    still inside the sliding view). This smooths the fixed-window edge burst
    while using almost no memory — the approach Cloudflare popularised.
    """

    def __init__(self, limit, window_seconds, clock=None):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock or SystemClock()
        self._counts = defaultdict(dict)  # per client: {window_id: count}

    def allow(self, key="global"):
        now = self.clock.now()
        window_id = int(now // self.window)
        elapsed = now - window_id * self.window
        # How much of the PREVIOUS window still overlaps the rolling view.
        prev_weight = (self.window - elapsed) / self.window

        counts = self._counts[key]
        current = counts.get(window_id, 0)
        previous = counts.get(window_id - 1, 0)
        estimated = current + previous * prev_weight

        if estimated < self.limit:
            counts[window_id] = current + 1
            # Forget windows older than the previous one — keeps memory tiny.
            for old in [w for w in counts if w < window_id - 1]:
                del counts[old]
            return True
        return False


# ---------------------------------------------------------------------------
# Demo: run the same burst through all four with a fake clock.
# ---------------------------------------------------------------------------
def cmd_demo(args):
    print("Scenario: limit ~5 requests per 10 seconds.")
    print("We fire 8 requests fast (t=0..1.4s), wait, then fire 4 more.\n")

    clock = FakeClock()
    limiters = {
        "TokenBucket(cap=5, 0.5/s)": TokenBucket(5, 0.5, clock),
        "FixedWindow(5 / 10s)": FixedWindow(5, 10, clock),
        "SlidingWindowLog(5 / 10s)": SlidingWindowLog(5, 10, clock),
        "SlidingWindowCounter(5 / 10s)": SlidingWindowCounter(5, 10, clock),
    }

    # A timeline of request times (seconds).
    burst1 = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4]
    burst2_at = 12.0
    burst2 = [12.0, 12.2, 12.4, 12.6]

    for name, limiter in limiters.items():
        clock.t = 0.0
        marks = []
        for t in burst1:
            clock.t = t
            marks.append("✓" if limiter.allow("alice") else "·")
        # jump ahead, then the second burst
        for t in burst2:
            clock.t = t
            marks.append("✓" if limiter.allow("alice") else "·")
        allowed = marks.count("✓")
        print(f"  {name:<32} {''.join(marks)}   ({allowed} allowed)")

    print("\n  ✓ = allowed   · = rejected")
    print("  First 8 marks are the fast burst; last 4 are after a ~11s pause.")
    print("\nNotice how token bucket allows a burst of 5 then trickles, while the")
    print("window algorithms reset once the old requests age out of the window.")


def main():
    parser = argparse.ArgumentParser(description="Rate limiting algorithms from scratch.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_demo = sub.add_parser("demo", help="watch all four handle the same burst")
    p_demo.set_defaults(func=cmd_demo)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
