"""Backfill timing accounts for work, cleanup, failures, and CLI overhead."""

import builtins
import importlib
import re

import pytest

from repo_history import _terminal, _timing, cli, history, state, writing

from .test_history import NOW
from .test_history import session as session

backfill = importlib.import_module("repo_history.backfill")


@pytest.mark.parametrize(
    "failure", [None, "preparation", "authentication", "collection", "monthly", "state"]
)
def test_phase_durations_reconcile_with_cli_runtime(
    session, tmp_path, monkeypatch, capsys, failure
):
    monkeypatch.setenv("GH_TOKEN", "test")
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    clock = [0.0]
    for module in (backfill, history, cli, _timing):
        monkeypatch.setattr(module, "perf_counter", lambda: clock[0])

    def delay(module, name, seconds, *, fail=False):
        original = getattr(module, name)

        def run(*args, **kwargs):
            clock[0] += seconds
            if fail:
                raise OSError("timing failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, run)

    delay(cli, "_parser", 19)
    delay(_terminal.BackfillProgress, "__exit__", 23)
    delay(backfill, "_check_directory", 2, fail=failure == "preparation")
    delay(history, "GraphQLClient", 3, fail=failure == "authentication")
    delay(history, "lookup_repository", 29, fail=failure == "collection")
    delay(session, "close", 4)
    delay(writing, "directory_repository", 5)
    delay(backfill, "history_to_months", 7, fail=failure == "monthly")
    delay(backfill, "_write_report", 11)
    delay(state, "write_collection_state", 17, fail=failure == "state")

    def saved(*args, **kwargs):
        clock[0] += 13
        builtins.print(*args, **kwargs)

    monkeypatch.setattr(cli, "print", saved, raising=False)
    assert cli.main(
        [
            "backfill",
            "o/r",
            "--start",
            "2024-01",
            "--end",
            "2024-03",
            "--output-directory",
            str(tmp_path),
        ]
    ) == (1 if failure else 0)
    output = capsys.readouterr().err
    phases = re.findall(
        r"INFO: (Backfill preparation|History collection|History grouping/writing monthly|"
        r"History inventory/verification) status=(\w+) elapsed_seconds=([\d.]+)",
        output,
    )
    expected = [("Backfill preparation", "completed", "2.000")]
    if failure == "preparation":
        expected[-1] = ("Backfill preparation", "failed", "2.000")
    elif failure == "authentication":
        expected.append(("History collection", "failed", "3.000"))
    else:
        expected.append(
            (
                "History collection",
                "failed" if failure == "collection" else "completed",
                "36.000",
            )
        )
        if failure != "collection":
            expected.append(
                (
                    "History grouping/writing monthly",
                    "failed" if failure == "monthly" else "completed",
                    "12.000" if failure == "monthly" else "84.000",
                )
            )
            if failure != "monthly":
                expected.append(
                    (
                        "History inventory/verification",
                        "failed" if failure == "state" else "completed",
                        "17.000",
                    )
                )
    assert phases == expected
    summaries = re.findall(
        r"Backfill summary status=(\w+) total_elapsed_seconds=([\d.]+) "
        r"phase_elapsed_seconds=([\d.]+) unaccounted_seconds=([\d.]+)",
        output,
    )
    measured = sum(float(seconds) for _, _, seconds in phases)
    assert summaries == [
        (
            "failed" if failure else "completed",
            f"{clock[0]:.3f}",
            f"{measured:.3f}",
            "42.000",
        )
    ]
    assert clock[0] == measured + 42
    assert "History grouped" not in output
    if failure not in {"preparation", "authentication"}:
        assert session.closed
