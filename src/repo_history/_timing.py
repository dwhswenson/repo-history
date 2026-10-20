"""Non-overlapping collection phase durations and an enclosing runtime summary."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

_PHASES: ContextVar[list[float] | None] = ContextVar(
    "collect_phase_times", default=None
)


def phase_elapsed(started):
    elapsed = perf_counter() - started
    phases = _PHASES.get()
    if phases is not None:
        phases.append(elapsed)
    return elapsed


@contextmanager
def timed_phase(logger, name):
    started = perf_counter()
    completed = False
    try:
        yield
        completed = True
    finally:
        logger.info(
            "%s status=%s elapsed_seconds=%.3f",
            name,
            "completed" if completed else "failed",
            phase_elapsed(started),
        )


@contextmanager
def collect_timing(logger, *, started=None):
    # The CLI owns the outer scope so its setup and display cleanup are included.
    if _PHASES.get() is not None:
        yield
        return
    started = perf_counter() if started is None else started
    phases = []
    token = _PHASES.set(phases)
    completed = False
    try:
        yield
        completed = True
    finally:
        _PHASES.reset(token)
        elapsed = perf_counter() - started
        measured = sum(phases)
        logger.info(
            "Collect summary status=%s total_elapsed_seconds=%.3f "
            "phase_elapsed_seconds=%.3f unaccounted_seconds=%.3f",
            "completed" if completed else "failed",
            elapsed,
            measured,
            elapsed - measured,
        )
