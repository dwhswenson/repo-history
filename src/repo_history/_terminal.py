"""CLI-owned Rich displays and throttled plain command progress."""

import logging
from contextlib import AbstractContextManager
from dataclasses import dataclass
from time import monotonic

from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from ._progress import Event, Usage, listen

LOG = logging.getLogger(__name__)


class _StatusSpinner(SpinnerColumn):
    def render(self, task):
        status = task.fields["status"]
        if status == "running":
            return super().render(task)
        return Text(
            {"completed": "✓", "skipped": "–", "failed": "×", "interrupted": "!"}.get(
                status, "·"
            )
        )


class _Counts(ProgressColumn):
    def render(self, task):
        if task.fields["status"] == "pending":
            return Text("—", style="dim")
        completed = int(task.completed)
        if task.fields["status"] == "skipped":
            return Text("—", style="dim")
        total = task.fields["reported_total"]
        if total is None:
            return Text(f"{completed}/?", style="cyan")
        percent = (
            100 * completed / total
            if total
            else (100 if task.fields["status"] == "completed" else 0)
        )
        # Exhausting the iterator, rather than the advisory total, completes a stage.
        if task.fields["status"] != "completed":
            percent = min(percent, 99)
        return Text(f"{completed}/{int(total)} {percent:.0f}%", style="cyan")


class _StageBar(BarColumn):
    def render(self, task):
        if task.fields["status"] in {"pending", "skipped"}:
            return Text("")
        return super().render(task)


class _Elapsed(TimeElapsedColumn):
    def render(self, task):
        if task.fields["status"] in {"pending", "skipped"}:
            return Text("")
        return super().render(task)


class _ETA(TimeRemainingColumn):
    def render(self, task):
        if task.fields["status"] != "running" or task.total is None:
            return Text("")
        return Text.assemble("ETA ~ ", super().render(task))


@dataclass
class _Stage:
    completed: int = 0
    total: int | None = None
    status: str = "pending"


class _ConsoleHandler(logging.Handler):
    def __init__(self, console):
        super().__init__()
        self.console = console

    def emit(self, record):
        self.console.print(self.format(record), markup=False, highlight=False)


class _CommandProgress(AbstractContextManager):
    label: str
    stage_names: tuple[str, ...]
    preparation: str
    show_usage = False

    def __init__(self, stream, *, verbose=False):
        self.console = Console(file=stream, highlight=False, markup=False)
        self.interactive = (
            self.console.is_terminal
            and not self.console.is_dumb_terminal
            and not verbose
        )
        self.stages = {name: _Stage() for name in self.stage_names}
        self.active = "Preparing"
        self.item = ""
        self.detail = self.preparation
        self.usage = Usage()
        self.last_log = monotonic()
        self.activity = Spinner("dots")
        self.progress = Progress(
            _StatusSpinner(),
            TextColumn("{task.description}"),
            _StageBar(bar_width=None),
            _Counts(),
            _Elapsed(),
            _ETA(),
            TextColumn("{task.fields[status]}"),
            console=self.console,
            auto_refresh=False,
            expand=True,
        )
        self.tasks = {
            name: self.progress.add_task(
                name, total=None, start=False, status="pending", reported_total=None
            )
            for name in self.stages
        }
        # get_renderable keeps elapsed time and activity animated during blocking I/O.
        self.live = (
            Live(
                console=self.console,
                get_renderable=self._render,
                refresh_per_second=4,
                redirect_stdout=False,
                redirect_stderr=False,
            )
            if self.interactive
            else None
        )

    def _budget(self):
        usage = self.usage
        remaining = "unknown" if usage.remaining is None else f"{usage.remaining:g}"
        reset = (
            "unknown"
            if usage.reset is None
            else usage.reset.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        )
        return (
            f"GraphQL {usage.points} points spent · "
            f"budget (last observed): {remaining} remaining, reset {reset} · "
            f"{usage.attempts} requests, {usage.retries} retries, "
            f"{usage.unmeasured} unmeasured attempts"
        )

    def _render(self):
        work = Table.grid(padding=(0, 1))
        work.add_column(width=2)
        work.add_column(ratio=1)
        work.add_row(
            self.activity if self.active == "Preparing" else Text(""),
            Text(f"{self.active}: {self.detail}"),
        )
        rows = [self.progress, work]
        if self.show_usage:
            rows.append(Text(self._budget(), style="dim"))
        return Group(*rows)

    def _plain(self):
        state = self.stages.get(self.active)
        counts = ""
        if state is not None:
            total = "?" if state.total is None else str(state.total)
            counts = f" {state.completed}/{total} {state.status}"
        usage = f" | {self._budget()}" if self.show_usage else ""
        LOG.info("%s %s%s: %s%s", self.label, self.active, counts, self.detail, usage)
        self.last_log = monotonic()

    def __enter__(self):
        self.listener = listen(self)
        self.listener.__enter__()
        try:
            if self.live is not None:
                self.live.start()
            else:
                self._plain()
        except BaseException:
            try:
                if self.live is not None:
                    self.live.stop()
            finally:
                self.listener.__exit__(None, None, None)
            raise
        return self

    def __call__(self, event: Event):
        state = self.stages.get(event.stage)
        important = event.kind in {
            "start",
            "complete",
            "skipped",
            "phase",
            "retry",
            "finish",
        }
        if event.usage is not None:
            self.usage = event.usage
        if event.kind in {"start", "skipped"}:
            self.active = event.stage
            self.item = ""
            self.detail = event.detail or "Processing"
            state.status = "skipped" if event.kind == "skipped" else "running"
            if state.status == "running":
                self.progress.start_task(self.tasks[event.stage])
        elif event.kind == "total":
            if type(event.total) is int and event.total >= 0:
                state.total = event.total
        elif event.kind == "advance":
            state.completed += 1
            if state.total is not None and state.completed > state.total:
                state.total = None
        elif event.kind == "complete":
            state.status = "completed"
            state.total = state.completed
            self.detail = event.detail or "Completed"
        elif event.kind in {"phase", "finish"}:
            self.active = event.stage
            self.item = ""
            self.detail = event.detail
        elif event.kind == "detail":
            self.item = event.detail
            self.detail = event.detail
        elif event.detail:
            self.detail = f"{self.item}: {event.detail}" if self.item else event.detail
        if state is not None:
            task = self.progress.tasks[self.tasks[event.stage]]
            # Progress.update(total=None) means "keep total"; assignment resets it.
            task.total = (
                max(state.total, state.completed / 0.99, 1)
                if state.total is not None and state.status == "running"
                else state.total
            )
            self.progress.update(
                task.id,
                completed=state.completed,
                status=state.status,
                reported_total=state.total,
            )
            if state.status in {"completed", "skipped"}:
                self.progress.stop_task(task.id)
        if self.live is None:
            if important or monotonic() - self.last_log >= 30:
                self._plain()
        if event.kind == "finish" and self.live is not None:
            self._plain()

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is not None:
                status = (
                    "interrupted"
                    if issubclass(exc_type, KeyboardInterrupt)
                    else "failed"
                )
                if self.active in self.stages:
                    state = self.stages[self.active]
                    if state.status == "running":
                        state.status = status
                        task_id = self.tasks[self.active]
                        self.progress.update(task_id, status=status)
                        self.progress.stop_task(task_id)
                self.detail = status
                self._plain()
        finally:
            try:
                if self.live is not None:
                    self.live.stop()
            finally:
                self.listener.__exit__(exc_type, exc, traceback)

    def log_handler(self):
        return _ConsoleHandler(self.console) if self.interactive else None


class BackfillProgress(_CommandProgress):
    label = "Backfill"
    stage_names = ("Issues", "PRs", "Commits", "Grouping", "Writing")
    preparation = "Validating destination and looking up repository"
    show_usage = True


class SummarizeProgress(_CommandProgress):
    label = "Summarize"
    stage_names = ("Reports",)
    preparation = "Finding monthly reports"
