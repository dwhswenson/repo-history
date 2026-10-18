"""Backfill telemetry, terminal lifecycle, and plain progress behavior."""

import io
import logging
import re
from datetime import datetime, timezone

import pytest
import requests

from repo_history import GraphQLError
from repo_history import _terminal as terminal
from repo_history._progress import Usage, emit, enabled, listen
from repo_history._terminal import BackfillProgress
from repo_history.queries import HISTORY_ISSUES_QUERY

from .helpers import Response, errors
from .test_collection import connection


class TTY(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def terminal_environment(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")


def test_listener_restores_nested_context_on_error():
    first, second = [], []
    assert not enabled()
    with listen(first.append):
        with pytest.raises(ValueError), listen(second.append):
            emit("start", stage="Issues")
            raise ValueError()
        emit("complete", stage="Issues")
    assert not enabled()
    assert [e.kind for e in first] == ["complete"]
    assert [e.kind for e in second] == ["start"]


@pytest.mark.parametrize("active", [False, True])
def test_totals_only_requested_on_first_page(client, active):
    first = connection("issues", [{"number": 1}], more=True, cursor="next")
    first.body["data"]["node"]["issues"]["totalCount"] = 2
    second = connection("issues", [{"number": 2}])
    second.body["data"]["node"]["issues"]["totalCount"] = 999
    client._session.responses = [errors("MAX_NODE_LIMIT_EXCEEDED"), first, second]
    events = []
    with listen(events.append if active else None):
        values = list(
            client.iter_connection(
                HISTORY_ISSUES_QUERY,
                {"id": "R"},
                operation_name="RepositoryIssues",
                connection_path=("node", "issues"),
                page_size=20,
            )
        )
    assert len(values) == 2
    variables = [call[1]["json"]["variables"] for call in client._session.calls]
    if active:
        assert [v["includeProgressTotal"] for v in variables] == [True, True, False]
        assert [(e.stage, e.total) for e in events if e.kind == "total"] == [
            ("Issues", 2)
        ]
        assert not any(e.kind == "advance" for e in events)
    else:
        assert all("includeProgressTotal" not in v for v in variables)
        assert not events


def test_usage_snapshots_cover_every_attempt_and_preserve_budget(client):
    first = Response(
        {"data": {"rateLimit": {"cost": 2}}},
        headers={
            "X-RateLimit-Remaining": "98",
            "X-RateLimit-Reset": "2000",
            "X-RateLimit-Used": "4000",
        },
    )
    denied = errors("FORBIDDEN")
    denied.body["data"]["rateLimit"] = {"cost": 4}
    client._session.responses = [
        first,
        requests.Timeout(),
        Response(status=503),
        Response(
            {"data": {"rateLimit": {"cost": 3}}},
            headers={
                "X-RateLimit-Remaining": "4997",
                "X-RateLimit-Reset": "6000",
            },
        ),
        denied,
    ]
    events = []
    with listen(events.append):
        client.execute("query Test { viewer { login } }", {}, operation_name="Test")
        client.execute("query Test { viewer { login } }", {}, operation_name="Test")
        with pytest.raises(GraphQLError):
            client.execute("query Test { viewer { login } }", {}, operation_name="Test")
    usage = [e.usage for e in events if e.kind == "usage"]
    assert [u.points for u in usage] == [2, 2, 2, 5, 9]
    assert [u.attempts for u in usage] == [1, 2, 3, 4, 5]
    assert [u.retries for u in usage] == [0, 0, 1, 2, 2]
    assert [u.unmeasured for u in usage] == [0, 1, 2, 2, 2]
    assert [u.remaining for u in usage] == [98, 98, 98, 4997, 4997]
    assert usage[-1].reset == datetime.fromtimestamp(6000, timezone.utc)
    assert len([e for e in events if e.kind == "retry"]) == 2


@pytest.mark.parametrize("invalid", [None, True, -1, "2", 2.5])
def test_invalid_totals_are_advisory(invalid):
    display = BackfillProgress(io.StringIO())
    with display:
        emit("start", stage="Issues")
        emit("total", stage="Issues", total=invalid)
        emit("advance", stage="Issues")
        assert display.stages["Issues"].total is None
        emit("complete", stage="Issues")
    assert display.stages["Issues"].completed == 1
    assert display.stages["Issues"].status == "completed"


def test_underestimated_total_does_not_complete_stage():
    display = BackfillProgress(io.StringIO())
    with display:
        emit("start", stage="PRs")
        emit("total", stage="PRs", total=1)
        emit("advance", stage="PRs")
        assert not display.progress.tasks[display.tasks["PRs"]].finished
        emit("advance", stage="PRs")
        assert display.stages["PRs"].total is None
        assert display.stages["PRs"].status == "running"
        emit("complete", stage="PRs")
    assert display.stages["PRs"].total == 2


def test_plain_updates_throttled_but_transitions_and_retries_immediate(
    monkeypatch, caplog
):
    now = [0]
    monkeypatch.setattr(terminal, "monotonic", lambda: now[0])
    caplog.set_level(logging.INFO, logger="repo_history._terminal")
    with BackfillProgress(io.StringIO()):
        emit("start", stage="Issues")
        emit("total", stage="Issues", total=10)
        caplog.clear()
        emit("advance", stage="Issues")
        now[0] = 29
        emit("usage", usage=Usage(points=9, attempts=2))
        assert not caplog.records
        now[0] = 30
        emit("advance", stage="Issues")
        assert len(caplog.records) == 1
        assert "Issues 2/10" in caplog.text and "GraphQL 9 points" in caplog.text
        emit("retry", detail="Retry in 1s")
        emit("complete", stage="Issues")
        assert len(caplog.records) == 3
    assert not enabled()


@pytest.mark.parametrize(
    "exception,status", [(ValueError, "failed"), (KeyboardInterrupt, "interrupted")]
)
def test_failed_display_cleans_up_and_preserves_completed_count(exception, status):
    stream = TTY()
    display = BackfillProgress(stream)
    with pytest.raises(exception), display:
        emit("start", stage="PRs")
        emit("total", stage="PRs", total=10)
        emit("advance", stage="PRs")
        raise exception()
    assert display.stages["PRs"].completed == 1
    assert display.stages["PRs"].status == status
    assert status in stream.getvalue()
    assert not display.live.is_started
    assert not enabled()


def test_interactive_details_logs_and_stdout_stay_separate(capsys):
    stream = TTY()
    display = BackfillProgress(stream)
    display.console.width = 60
    handler = display.log_handler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    with display:
        emit("skipped", stage="Issues", detail="Issues are disabled")
        emit("start", stage="PRs")
        emit("detail", detail="PR #42")
        emit("request", detail="PullRequestReviews page=2 attempt=1")
        assert "PR #42" in display.detail and "page=2" in display.detail
        emit("usage", usage=Usage(points=12, attempts=3, unmeasured=1))
        handler.handle(
            logging.LogRecord(
                "test", logging.INFO, "", 1, "example [literal] log", (), None
            )
        )
        emit("complete", stage="PRs")
        emit("skipped", stage="Commits", detail="No default-branch commits")
        emit("start", stage="Grouping", detail="Validating reports")
        emit("total", stage="Grouping", total=1)
        emit("advance", stage="Grouping")
        emit("complete", stage="Grouping")
        emit("start", stage="Writing", detail="Writing reports")
        emit("total", stage="Writing", total=1)
        emit("detail", detail="saved.json")
        assert display.live.is_started
        assert display.detail == "saved.json"
        emit("advance", stage="Writing")
        emit("complete", stage="Writing")
        assert display.stages["Writing"].completed == 1
        assert display.stages["Grouping"].completed == 1
        # Redirected stdout stays independent of live stderr.
        print("saved.json", flush=True)
    assert capsys.readouterr().out == "saved.json\n"
    output = stream.getvalue()
    output = " ".join(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output).split())
    assert "[literal]" in output
    assert "12 points spent" in output and "last observed" in output
    assert "1 unmeasured" in output and "skipped" in output
    assert not enabled()


def test_verbose_uses_plain_output_even_on_tty():
    display = BackfillProgress(TTY(), verbose=True)
    assert display.live is None
    assert display.log_handler() is None


def test_dumb_terminal_uses_plain_output(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert BackfillProgress(TTY()).live is None


def test_failed_start_restores_context(monkeypatch):
    display = BackfillProgress(TTY())
    monkeypatch.setattr(display.live, "start", lambda: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        display.__enter__()
    assert not enabled()
