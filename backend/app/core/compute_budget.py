"""Keeps the Test Bench's CPU-bound work from starving the live telemetry broadcast.

The problem this solves is specific and was measured, not anticipated. Scenario simulation
and operating-point optimisation are seconds of tight, pure-Python numeric work. Handing
them to `run_in_threadpool` takes them off the event loop's *ordering*, which is necessary
but not sufficient: they still hold the GIL, and the loop's 10 Hz broadcast coroutine has
to win it back to send anything.

With one heavy request in flight the effect is invisible — the stream held 9 Hz with a
125 ms worst-case gap. With two (a scenario and an optimiser search at once, which is one
impatient operator away) it degraded to 6.7 Hz with a **1 048 ms** gap: a full second where
every connected dashboard is frozen. Nothing dropped and the stream recovered immediately,
but a second of stale telemetry on a live monitoring page is not something to shrug at.

Two measures, both cheap:

**Serialise heavy work.** `heavy_compute_slot()` admits one at a time. Two multi-second
searches on one core make each other slower anyway — there is no throughput to lose — and
it bounds GIL contention to the single-competitor case that measured fine. A queued request
waits rather than failing, because the caller has already committed to waiting several
seconds and a 409 would be a worse answer than a slower one.

**Rotate the GIL faster.** CPython's default 5 ms switch interval is tuned for throughput.
Dropping it to 1 ms costs a little in a tight loop and buys the broadcast coroutine five
times as many chances to run during one. For a system whose whole point is a live feed,
that is the right side of the trade.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

#: Only one scenario or optimiser search executes at a time. Deliberately not a config
#: knob: raising it does not make the machine faster, it only makes the live stream worse.
MAX_CONCURRENT_HEAVY_COMPUTE = 1

GIL_SWITCH_INTERVAL_S = 0.001

_semaphore: asyncio.Semaphore | None = None


def tune_interpreter() -> None:
    """Shorten the GIL switch interval. Called once from the app's lifespan."""
    previous = sys.getswitchinterval()
    if previous > GIL_SWITCH_INTERVAL_S:
        sys.setswitchinterval(GIL_SWITCH_INTERVAL_S)
        logger.info(
            "GIL switch interval %.0f ms -> %.0f ms so the telemetry broadcast stays "
            "responsive while Test Bench computations run.",
            previous * 1000,
            GIL_SWITCH_INTERVAL_S * 1000,
        )


def _get_semaphore() -> asyncio.Semaphore:
    # Created lazily so it binds to the running loop rather than to import time.
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_HEAVY_COMPUTE)
    return _semaphore


def yield_to_event_loop() -> None:
    """Hand the GIL over, so the live telemetry broadcast gets a turn.

    A shortened switch interval only makes CPython *check* for waiting threads more often;
    the waiting thread still has to win the handoff, and against a long-running pure-Python
    loop it frequently loses several times in a row. On a 16-core, lightly loaded host that
    convoying still cost the broadcast a 418 ms worst-case gap — this is not the OS running
    out of cores, it is one interpreter lock.

    `time.sleep(0)` releases the lock outright and yields to the scheduler, which is the
    standard way out. It costs well under a microsecond, so calling it once per scenario
    sample or once per optimiser evaluation is free at these call rates, and it bounds how
    long the event loop can be locked out to roughly one of those units of work.
    """
    time.sleep(0)


@asynccontextmanager
async def heavy_compute_slot(label: str):
    """Admit one CPU-bound Test Bench request at a time."""
    semaphore = _get_semaphore()
    queued = semaphore.locked()
    waited_from = time.perf_counter()
    async with semaphore:
        if queued:
            logger.info(
                "%s waited %.1f s for a compute slot", label, time.perf_counter() - waited_from
            )
        yield
