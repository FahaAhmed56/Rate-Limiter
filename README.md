# Rate Limiter

The four classic rate-limiting algorithms, built from scratch in pure Python —
**token bucket, fixed window, sliding window log, and sliding window counter**.

Rate limiting is what stands between an API and getting hammered — by abuse, a
runaway client, or a thundering herd after an outage. It's a staple of
system-design interviews and lives in every real API gateway. There's no single
"best" algorithm; each trades accuracy against memory and burst behaviour, and
this project shows all four side by side.

Pure standard library — **nothing to install**.

## See them in action

Needs [Python](https://www.python.org) 3.8+.

```bash
python ratelimit.py demo
```

It fires the same burst of requests at all four limiters and prints a timeline:

```
  TokenBucket(cap=5, 0.5/s)        ✓✓✓✓✓···✓✓✓✓   (9 allowed)
  FixedWindow(5 / 10s)             ✓✓✓✓✓···✓✓✓✓   (9 allowed)
  SlidingWindowLog(5 / 10s)        ✓✓✓✓✓···✓✓✓✓   (9 allowed)
  SlidingWindowCounter(5 / 10s)    ✓✓✓✓✓···✓✓··   (7 allowed)
```

## Use it in code

```python
from ratelimit import TokenBucket

# Allow bursts of 10, refilling 2 per second, per client key.
limiter = TokenBucket(capacity=10, refill_rate=2)

if limiter.allow("user:123"):
    handle_request()
else:
    return "429 Too Many Requests"
```

All four take a `key` so each client (user, IP, API key) is limited separately.

## The four algorithms

| Algorithm | Idea | Strength | Weakness |
| --- | --- | --- | --- |
| **Token bucket** | A bucket of tokens refills steadily; each request spends one | Smooth rate *and* allows bursts | slightly more state |
| **Fixed window** | Count requests per fixed clock window | Dead simple, tiny | up to **2× burst** at window edges |
| **Sliding window log** | Store a timestamp per recent request | Perfectly exact | stores every timestamp (memory) |
| **Sliding window counter** | Weighted blend of current + previous window counts | Near-exact, tiny memory | an approximation |

The **fixed-window edge burst** is the famous gotcha: a client can send the full
limit at the end of one window and the full limit again at the start of the
next — 2× the intended rate across the boundary. The sliding approaches exist to
fix exactly that, and the tests demonstrate both the flaw and the fix.

## The design trick: an injectable clock

Rate limiters are all about *time*, and time-based code is normally a pain to
test (you'd have to actually sleep). The fix is to make "what time is it?" a
dependency:

- `SystemClock` — the real one, used in production (monotonic, so it can't jump
  backward on an NTP resync).
- `FakeClock` — used in tests; you advance it by hand with `clock.advance(2)`.

That one bit of dependency injection is why the whole test suite is exact,
instant, and never flaky. It's a pattern worth stealing for any time- or
randomness-driven code.

## Run the tests

```bash
python test_ratelimit.py       # no dependencies
# or, with pytest:
python -m pytest
```

They drive a `FakeClock` through precise scenarios: the token bucket bursting
and refilling, the fixed-window edge-burst flaw, the sliding log being exact,
and the sliding counter's weighted estimate (fill a window, then jump to its
midpoint and confirm exactly half the allowance is used).

## How it's built

- `SystemClock` / `FakeClock` — the injectable time source
- `TokenBucket` — capacity + refill rate, per key
- `FixedWindow` — a counter per fixed window, per key
- `SlidingWindowLog` — a deque of timestamps, per key
- `SlidingWindowCounter` — current + weighted previous window counts
- `cmd_demo` — the side-by-side timeline

## Ideas for later

- Wrap it as middleware for a small Flask/FastAPI app, returning HTTP 429
- Return `Retry-After` — how many seconds until the next request would pass
- Back it with Redis so limits are shared across many servers (the real-world
  version, since one process's counters don't help a fleet)
- A distributed token bucket using Redis + Lua for atomicity
