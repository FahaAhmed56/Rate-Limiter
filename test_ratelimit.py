"""Tests for ratelimit.py.

Run with:  python test_ratelimit.py       (no dependencies)
       or:  python -m pytest

Every test drives a FakeClock by hand, so the time-based behaviour is exact
and reproducible — no real sleeping, no flakiness.
"""

from ratelimit import (
    FakeClock, TokenBucket, FixedWindow, SlidingWindowLog, SlidingWindowCounter,
)


# ---- Token bucket ----
def test_token_bucket_allows_a_burst_up_to_capacity():
    clock = FakeClock()
    tb = TokenBucket(capacity=5, refill_rate=1, clock=clock)
    # Five fast requests drain the full bucket...
    assert [tb.allow() for _ in range(5)] == [True] * 5
    # ...the sixth has no token left.
    assert tb.allow() is False


def test_token_bucket_refills_over_time():
    clock = FakeClock()
    tb = TokenBucket(capacity=5, refill_rate=1, clock=clock)  # 1 token/sec
    for _ in range(5):
        tb.allow()          # drain it
    assert tb.allow() is False
    clock.advance(2)         # 2 seconds -> 2 tokens back
    assert tb.allow() is True
    assert tb.allow() is True
    assert tb.allow() is False   # only 2 had refilled


def test_token_bucket_never_exceeds_capacity():
    clock = FakeClock()
    tb = TokenBucket(capacity=3, refill_rate=1, clock=clock)
    clock.advance(1000)      # idle a long time
    # Even so, only `capacity` tokens are available — they don't pile up forever.
    assert [tb.allow() for _ in range(4)] == [True, True, True, False]


def test_token_bucket_is_per_key():
    clock = FakeClock()
    tb = TokenBucket(capacity=2, refill_rate=1, clock=clock)
    assert tb.allow("alice") is True
    assert tb.allow("alice") is True
    assert tb.allow("alice") is False   # alice is out
    assert tb.allow("bob") is True      # bob has his own bucket


# ---- Fixed window ----
def test_fixed_window_limits_then_resets():
    clock = FakeClock()
    fw = FixedWindow(limit=3, window_seconds=10, clock=clock)
    assert [fw.allow() for _ in range(3)] == [True, True, True]
    assert fw.allow() is False           # limit hit within the window
    clock.advance(10)                    # next window
    assert fw.allow() is True            # counter reset


def test_fixed_window_edge_burst_flaw():
    """The classic weakness: up to 2x the limit can cross a window boundary."""
    clock = FakeClock(start=9.0)         # near the end of window 0
    fw = FixedWindow(limit=3, window_seconds=10, clock=clock)
    assert [fw.allow() for _ in range(3)] == [True, True, True]  # 3 at t=9
    clock.advance(1.0)                   # t=10 -> window 1 begins
    assert [fw.allow() for _ in range(3)] == [True, True, True]  # 3 more at t=10
    # 6 requests in a ~1-second span, despite a "3 per 10s" limit.


# ---- Sliding window log ----
def test_sliding_log_is_exact():
    clock = FakeClock()
    sl = SlidingWindowLog(limit=3, window_seconds=10, clock=clock)
    clock.t = 0.0; assert sl.allow() is True
    clock.t = 1.0; assert sl.allow() is True
    clock.t = 2.0; assert sl.allow() is True
    clock.t = 3.0; assert sl.allow() is False     # 3 already in the last 10s
    # Advance so the FIRST request (t=0) slides out of the window.
    clock.t = 10.5
    assert sl.allow() is True                      # room again


def test_sliding_log_no_edge_burst():
    """Unlike fixed window, you can never fit 2x the limit in one window span."""
    clock = FakeClock(start=9.0)
    sl = SlidingWindowLog(limit=3, window_seconds=10, clock=clock)
    for _ in range(3):
        assert sl.allow() is True                  # 3 at t=9
    clock.advance(1.0)                             # t=10
    # The t=9 requests are still within the last 10s, so these are blocked.
    assert sl.allow() is False


# ---- Sliding window counter ----
def test_sliding_counter_behaves_like_fixed_within_first_window():
    clock = FakeClock()
    sc = SlidingWindowCounter(limit=10, window_seconds=10, clock=clock)
    # No previous window, so it just counts up to the limit.
    assert sum(sc.allow() for _ in range(10)) == 10
    assert sc.allow() is False


def test_sliding_counter_weights_the_previous_window():
    clock = FakeClock()
    sc = SlidingWindowCounter(limit=10, window_seconds=10, clock=clock)
    # Fill window 0 completely at t=0.
    for _ in range(10):
        assert sc.allow() is True
    # Jump to the middle of window 1: previous window counts at 50% weight,
    # so 10 * 0.5 = 5 are considered "still used" -> only 5 new allowed.
    clock.t = 15.0
    allowed = sum(sc.allow() for _ in range(10))
    assert allowed == 5, f"expected 5 under the weighted estimate, got {allowed}"


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {test.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} tests passed")
