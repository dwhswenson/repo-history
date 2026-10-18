"""CLI parsing, diagnostics, defaults, and removal of strategy flags."""

import importlib
import logging
from datetime import datetime, timezone

import pytest

from repo_history import (
    AuthenticationError,
    RateLimitError,
    RepositoryHistory,
    cli,
    update,
)

backfill_module = importlib.import_module("repo_history.backfill")
NOW = datetime(2024, 3, 1, tzinfo=timezone.utc)


@pytest.fixture
def collector(monkeypatch):
    calls = []

    def collect(owner, repo, **kwargs):
        calls.append((owner, repo, kwargs))
        logging.getLogger("repo_history.history").info(
            "GitHub API usage status=completed"
        )
        logging.getLogger("repo_history.history").debug("debug details")
        return RepositoryHistory(
            "R_example",
            "example/project",
            "https://github.com/example/project",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            None,
            None,
            NOW,
            NOW,
            (),
            (),
            (),
        )

    def snapshot(*args, **kwargs):
        return collect(*args, **kwargs), "bounded", "bounded"

    monkeypatch.setattr(backfill_module, "_collect_history", collect)
    monkeypatch.setattr(update, "_collect_snapshot", snapshot)
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)
    return calls


def run(directory, *args):
    return cli.main(
        [
            "update",
            "o/r",
            "--start",
            "2024-02",
            "--end",
            "2024-02",
            "--output-directory",
            str(directory),
            *args,
        ]
    )


def test_update_uses_one_bounded_interval(tmp_path, collector, capsys):
    assert run(tmp_path) == 0
    assert len(collector) == 1
    assert collector[0][2]["start"] == datetime(2024, 2, 1, tzinfo=timezone.utc)
    assert collector[0][2]["end"] == datetime(2024, 3, 1, tzinfo=timezone.utc)
    out, err = capsys.readouterr()
    assert out == str(tmp_path / "2024-02.json") + "\n"
    assert "Updating o/r 2024-02 through 2024-02" in err
    assert "GitHub API usage" in err
    assert "debug details" not in err


@pytest.mark.parametrize("flag", ["--quiet", "--verbose"])
def test_verbosity(tmp_path, collector, capsys, flag):
    assert run(tmp_path, flag) == 0
    out, err = capsys.readouterr()
    if flag == "--quiet":
        assert out == err == ""
    else:
        assert "2024-02.json" in out and "debug details" in err


@pytest.mark.parametrize("command", ["backfill", "update"])
@pytest.mark.parametrize(
    "option,value",
    [
        ("--mode", "monthly"),
        ("--mode", "history"),
        ("--mode", "range"),
        ("--discovery", "full"),
        ("--discovery", "bounded"),
    ],
)
def test_removed_strategy_flags(tmp_path, collector, command, option, value):
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "o/r", "--output-directory", str(tmp_path), option, value])
    assert exc.value.code == 2
    assert not collector


@pytest.mark.parametrize("command", ["backfill", "update"])
@pytest.mark.parametrize("repository", ["owner", "o/r/extra", "/r", "o/", " o/r"])
def test_bad_repository(tmp_path, collector, command, repository):
    with pytest.raises(SystemExit) as exc:
        cli.main([command, repository, "--output-directory", str(tmp_path)])
    assert exc.value.code == 2 and not collector


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["o/r"],
        ["manifest"],
        ["update", "o/r"],
        ["update", "o/r", "--start", "2024-2"],
        ["update", "o/r", "--start", "2024-13"],
        ["update", "o/r", "--start", "2024-04"],
        ["update", "o/r", "--start", "2024-03", "--end", "2024-02"],
        ["update", "o/r", "--quiet", "--verbose"],
        ["update", "o/r", "--month", "2024-02"],
    ],
)
def test_invalid_arguments(tmp_path, collector, arguments):
    if arguments == ["update", "o/r"]:
        argv = arguments
    else:
        argv = [*arguments, "--output-directory", str(tmp_path)]
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2 and not collector


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError("set GH_TOKEN"),
        OSError("disk full"),
        RateLimitError("rate limited", retry_after=30, reset_at=NOW),
    ],
)
def test_expected_errors(tmp_path, collector, monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(update, "_collect_snapshot", fail)
    assert run(tmp_path, "--quiet") == 1
    out, err = capsys.readouterr()
    assert not out and str(error) in err and "Traceback" not in err
    if isinstance(error, RateLimitError):
        assert "retry after 30 seconds" in err and "reset at 2024-03-01" in err


@pytest.mark.parametrize("command", [None, "backfill", "update", "summarize"])
def test_help(command, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(([command] if command else []) + ["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--mode" not in out and "--discovery" not in out
    if command is None:
        assert all(name in out for name in ("backfill", "update", "summarize"))
    elif command == "summarize":
        assert "--input-directory" in out
    else:
        assert "--start" in out and "--end" in out


def test_logging_configuration_restored(tmp_path, collector):
    logger = logging.getLogger("repo_history")
    original = logger.level, logger.propagate, logger.handlers[:]
    assert run(tmp_path) == 0
    assert (logger.level, logger.propagate, logger.handlers) == original


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2024, 2, 29, 23, 59, 59, tzinfo=timezone.utc), "2024-02"),
        (NOW, "2024-03"),
    ],
)
def test_default_end_at_utc_rollover(tmp_path, collector, monkeypatch, now, expected):
    monkeypatch.setattr(cli, "_utc_now", lambda: now)
    assert (
        cli.main(
            ["update", "o/r", "--start", "2024-01", "--output-directory", str(tmp_path)]
        )
        == 0
    )
    assert (tmp_path / f"{expected}.json").exists()
    assert len(collector) == 1


def test_real_collector_missing_authentication(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert run(tmp_path) == 1
    assert "GH_TOKEN" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "flags,progress",
    [
        ([], True),
        (["--verbose"], True),
        (["--no-progress"], False),
        (["--quiet"], False),
    ],
)
def test_backfill_progress_modes(
    tmp_path, collector, capsys, monkeypatch, flags, progress
):
    from repo_history._progress import enabled

    actual = backfill_module._collect_history
    active = []

    def collect(*args, **kwargs):
        active.append(enabled())
        return actual(*args, **kwargs)

    monkeypatch.setattr(backfill_module, "_collect_history", collect)
    assert (
        cli.main(
            [
                "backfill",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--output-directory",
                str(tmp_path),
                *flags,
            ]
        )
        == 0
    )
    out, err = capsys.readouterr()
    assert active == [progress]
    assert ("Backfill Preparing" in err) == progress
    assert ("points spent" in err) == progress
    assert "\x1b" not in err
    assert out == ("" if "--quiet" in flags else str(tmp_path / "2024-02.json") + "\n")
    assert not enabled()


def test_update_fallback_has_no_progress(tmp_path, collector, monkeypatch, capsys):
    from repo_history._progress import enabled

    actual = backfill_module._collect_history
    active = []

    def collect(*args, **kwargs):
        active.append(enabled())
        return actual(*args, **kwargs)

    monkeypatch.setattr(backfill_module, "_collect_history", collect)
    assert cli.main(["update", "o/r", "--output-directory", str(tmp_path)]) == 0
    assert active == [False]
    assert "Backfill Preparing" not in capsys.readouterr().err


@pytest.mark.parametrize("flags", [[], ["--verbose"], ["--no-progress"]])
def test_backfill_error_restores_progress_and_logging(
    tmp_path, collector, monkeypatch, capsys, flags
):
    from repo_history._progress import enabled

    def fail(*args, **kwargs):
        raise AuthenticationError("missing token")

    monkeypatch.setattr(backfill_module, "_collect_history", fail)
    logger = logging.getLogger("repo_history")
    original = logger.level, logger.propagate, logger.handlers[:]
    assert (
        cli.main(["backfill", "o/r", "--output-directory", str(tmp_path), *flags]) == 1
    )
    assert "missing token" in capsys.readouterr().err
    assert (logger.level, logger.propagate, logger.handlers) == original
    assert not enabled()


@pytest.mark.parametrize(
    "stdout_tty,stderr_tty,flags,paths_expected",
    [
        (True, True, [], False),
        (False, True, [], True),
        (True, False, [], True),
        (True, True, ["--verbose"], True),
        (True, True, ["--no-progress"], True),
    ],
)
def test_live_writing_replaces_terminal_paths_but_preserves_pipelines(
    tmp_path, collector, monkeypatch, stdout_tty, stderr_tty, flags, paths_expected
):
    import io

    class Stream(io.StringIO):
        def __init__(self, tty):
            super().__init__()
            self.tty = tty

        def isatty(self):
            return self.tty

    stdout, stderr = Stream(stdout_tty), Stream(stderr_tty)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    assert (
        cli.main(
            [
                "backfill",
                "o/r",
                "--start",
                "2024-02",
                "--end",
                "2024-02",
                "--output-directory",
                str(tmp_path),
                *flags,
            ]
        )
        == 0
    )
    expected = str(tmp_path / "2024-02.json") + "\n" if paths_expected else ""
    assert stdout.getvalue() == expected
    if stderr_tty and not flags:
        assert "Writing" in stderr.getvalue()
        assert "Saved 1 monthly reports" in stderr.getvalue()
