"""Private command telemetry; inactive unless a CLI listener is installed."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Usage:
    points: int = 0
    attempts: int = 0
    retries: int = 0
    unmeasured: int = 0
    remaining: float | None = None
    reset: datetime | None = None


@dataclass(frozen=True)
class Event:
    kind: str
    stage: str = ""
    detail: str = ""
    total: int | None = None
    usage: Usage | None = None


_LISTENER: ContextVar[Callable[[Event], None] | None] = ContextVar(
    "command_progress_listener", default=None
)


def enabled() -> bool:
    return _LISTENER.get() is not None


def emit(kind: str, **fields) -> None:
    listener = _LISTENER.get()
    if listener is not None:
        listener(Event(kind, **fields))


@contextmanager
def listen(listener: Callable[[Event], None] | None):
    token = _LISTENER.set(listener)
    try:
        yield
    finally:
        _LISTENER.reset(token)
