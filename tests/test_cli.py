"""Tests for CLI parsing, diagnostics, and incremental output."""

import importlib
import logging
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from repo_history import (
    AuthenticationError,
    RateLimitError,
    ReportWriteError,
    cli,
)
from repo_history.collection import _month_bounds

backfill_module = importlib.import_module("repo_history.backfill")


@pytest.fixture
def collector(monkeypatch, empty):
    calls = []

    def collect(owner, repo, year, month, **kwargs):
        calls.append((owner, repo, year, month, kwargs))
        start, end = _month_bounds(year, month)
        logging.getLogger("repo_history.collection").info(
            "GitHub API usage status=completed"
        )
        logging.getLogger("repo_history.collection").debug("debug details")
        return replace(
            empty,
            metadata=replace(
                empty.metadata,
                month=f"{year:04d}-{month:02d}",
                month_start=start,
                month_end=end,
                collection_started_at=end,
                collection_finished_at=end,
                ongoing=False,
            ),
        )

    monkeypatch.setattr(backfill_module, "collect_month", collect)

    def now():
        return datetime(2024, 3, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(cli, "_utc_now", now)
    monkeypatch.setattr(backfill_module, "_utc_now", now)
    return calls


def test_explicit_month_and_discovery(tmp_path, collector, capsys):
    assert (
        cli.main(
            [
                "update",
                "--mode",
                "monthly",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--discovery",
                "bounded",
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert collector == [("o", "r", 2024, 2, {"token": None, "discovery": "bounded"})]
    out, err = capsys.readouterr()
    assert out == str(tmp_path / "2024-02.json") + "\n"
    assert "Collecting o/r 2024-02" in err
    assert "GitHub API usage" in err
    assert "debug details" not in err


def test_default_month_uses_invocation_utc(tmp_path, collector, capsys):
    assert (
        cli.main(
            [
                "update",
                "--mode",
                "monthly",
                "o/r",
                "--start",
                "2024-03",
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert collector[0][2:4] == (2024, 3)
    assert "2024-03.json" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--quiet", "--verbose"])
def test_verbosity(tmp_path, collector, capsys, flag):
    assert (
        cli.main(
            [
                "update",
                "--mode",
                "monthly",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--output-directory",
                str(tmp_path),
                flag,
            ]
        )
        == 0
    )
    out, err = capsys.readouterr()
    if flag == "--quiet":
        assert out == err == ""
    else:
        assert "2024-02.json" in out
        assert "debug details" in err


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["owner"],
        ["o/r/extra"],
        ["/r"],
        ["o/"],
        [" o/r"],
        ["update", "--mode", "monthly", "o/r", "--start", "2024-2", "--end", "2024-2"],
        [
            "update",
            "--mode",
            "monthly",
            "o/r",
            "--start",
            "2024-13",
            "--end",
            "2024-13",
        ],
        [
            "update",
            "--mode",
            "monthly",
            "o/r",
            "--start",
            "2024-04",
            "--end",
            "2024-04",
        ],
        ["update", "o/r", "--discovery", "bad"],
        ["update", "o/r", "--quiet", "--verbose"],
        [
            "backfill",
            "--mode",
            "monthly",
            "o/r",
            "--start",
            "2024-03",
            "--end",
            "2024-02",
        ],
    ],
)
def test_invalid_arguments_exit_two_before_network(tmp_path, collector, arguments):
    with pytest.raises(SystemExit) as exc:
        cli.main([*arguments, "--output-directory", str(tmp_path)])
    assert exc.value.code == 2
    assert not collector


def test_output_directory_required(collector):
    with pytest.raises(SystemExit) as exc:
        cli.main(["update", "o/r"])
    assert exc.value.code == 2
    assert not collector


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError("set GH_TOKEN or GITHUB_TOKEN"),
        OSError("permission denied"),
        ReportWriteError("bad destination"),
        RateLimitError(
            "rate limited",
            retry_after=30,
            reset_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        ),
    ],
)
def test_expected_errors_exit_one(tmp_path, collector, monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(backfill_module, "collect_month", fail)
    assert (
        cli.main(
            [
                "update",
                "--mode",
                "monthly",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--output-directory",
                str(tmp_path),
                "--quiet",
            ]
        )
        == 1
    )
    out, err = capsys.readouterr()
    assert not out
    assert str(error) in err
    assert "Traceback" not in err
    if isinstance(error, RateLimitError):
        assert "retry after 30 seconds" in err
        assert "reset at 2024-03-01T00:00:00+00:00" in err


def test_backfill_prints_completed_month_before_failure(
    tmp_path, collector, monkeypatch, capsys
):
    collect = backfill_module.collect_month

    def fail_second(owner, repo, year, month, **kwargs):
        if month == 2:
            raise AuthenticationError("expired credential")
        return collect(owner, repo, year, month, **kwargs)

    monkeypatch.setattr(backfill_module, "collect_month", fail_second)
    result = cli.main(
        [
            "backfill",
            "--mode",
            "monthly",
            "o/r",
            "--start",
            "2024-01",
            "--end",
            "2024-03",
            "--discovery",
            "bounded",
            "--output-directory",
            str(tmp_path),
        ]
    )
    assert result == 1
    out, err = capsys.readouterr()
    assert out == str(tmp_path / "2024-01.json") + "\n"
    assert "Collecting o/r 2024-02" in err
    assert "expired credential" in err
    assert collector[0][4] == {"token": None, "discovery": "bounded"}


def test_bad_directory_prevents_collection(tmp_path, collector, capsys):
    (tmp_path / "2024-01.json").write_text("broken")
    assert (
        cli.main(
            [
                "update",
                "--mode",
                "monthly",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 1
    )
    assert not collector
    assert "2024-01.json" in capsys.readouterr().err


def test_logging_configuration_restored(tmp_path, collector):
    logger = logging.getLogger("repo_history")
    original = logger.level, logger.propagate, logger.handlers[:]
    assert (
        cli.main(
            [
                "update",
                "--mode",
                "monthly",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert (logger.level, logger.propagate, logger.handlers) == original


def test_real_collector_missing_authentication(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert (
        cli.main(
            [
                "update",
                "--mode",
                "monthly",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 1
    )
    out, err = capsys.readouterr()
    assert not out
    assert "GH_TOKEN" in err
    assert "Traceback" not in err
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2024, 2, 29, 23, 59, 59, tzinfo=timezone.utc), (2024, 2)),
        (datetime(2024, 3, 1, tzinfo=timezone.utc), (2024, 3)),
    ],
)
def test_default_utc_month_at_rollover(tmp_path, collector, monkeypatch, now, expected):
    times = iter([now])
    monkeypatch.setattr(cli, "_utc_now", lambda: next(times))
    from repo_history import report_to_json

    # Seed the preceding month so update recollects it and reaches invocation month.
    report = backfill_module.collect_month("o", "r", 2024, 1)
    (tmp_path / "2024-01.json").write_text(report_to_json(report))
    collector.clear()
    assert (
        cli.main(
            ["update", "--mode", "monthly", "o/r", "--output-directory", str(tmp_path)]
        )
        == 0
    )
    assert collector[-1][2:4] == expected


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ([], [(2024, 1), (2024, 2), (2024, 3)]),
        (["--start", "2024-02"], [(2024, 2), (2024, 3)]),
        (["--end", "2024-02"], [(2024, 1), (2024, 2)]),
    ],
)
def test_backfill_optional_endpoints(
    tmp_path, collector, monkeypatch, arguments, expected
):
    monkeypatch.setattr(
        backfill_module, "_lookup_start", lambda *args: ("R_example", "2024-01")
    )
    assert (
        cli.main(
            [
                "backfill",
                "--mode",
                "monthly",
                "o/r",
                *arguments,
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert [c[2:4] for c in collector] == expected
    assert all(c[4]["discovery"] == "bounded" for c in collector)


def test_explicit_full_override(tmp_path, collector):
    assert (
        cli.main(
            [
                "update",
                "--mode",
                "monthly",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--discovery",
                "full",
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert collector[0][4]["discovery"] == "full"


@pytest.mark.parametrize("command", ["backfill", "update"])
@pytest.mark.parametrize("repository", ["owner", "o/r/extra", "/r", "o/", " o/r"])
def test_bad_repository_in_subcommands(collector, command, repository, tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main([command, repository, "--output-directory", str(tmp_path)])
    assert exc.value.code == 2
    assert not collector


@pytest.mark.parametrize("command", [None, "backfill", "update", "summarize"])
def test_command_help(command, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(([command] if command else []) + ["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    if command is None:
        assert all(name in out for name in ("backfill", "update", "summarize"))
    elif command == "summarize":
        assert "--input-directory" in out
    else:
        assert all(
            flag in out for flag in ("--start", "--end", "--mode", "--discovery")
        )


@pytest.mark.parametrize(
    "arguments", [["manifest"], ["o/r"], ["update", "o/r", "--month", "2024-02"]]
)
def test_removed_entrypoints(collector, arguments, tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main([*arguments, "--output-directory", str(tmp_path)])
    assert exc.value.code == 2
    assert not collector


@pytest.mark.parametrize("discovery", ["full", "bounded"])
def test_update_monthly_fallback(tmp_path, collector, monkeypatch, discovery):
    monkeypatch.setattr(
        backfill_module, "_lookup_start", lambda *args: ("R_example", "2024-01")
    )
    assert (
        cli.main(
            [
                "update",
                "o/r",
                "--mode",
                "monthly",
                "--end",
                "2024-02",
                "--discovery",
                discovery,
                "--output-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert [c[2:4] for c in collector] == [(2024, 1), (2024, 2)]
    assert all(c[4]["discovery"] == discovery for c in collector)
