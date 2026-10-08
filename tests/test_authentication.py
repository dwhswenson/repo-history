"""Tests for repo_history_tracking.authentication."""

import pytest

import repo_history_tracking as api


def test_authentication(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(api.AuthenticationError):
        api.resolve_token()
    monkeypatch.setenv("GITHUB_TOKEN", " fallback ")
    assert api.resolve_token() == "fallback"
    monkeypatch.setenv("GH_TOKEN", " primary ")
    assert api.resolve_token() == "primary"
    assert api.resolve_token(" explicit ") == "explicit"
    monkeypatch.setenv("GH_TOKEN", "  ")
    assert api.resolve_token() == "fallback"
    for bad in ("", " ", 123):
        with pytest.raises(api.AuthenticationError):
            api.resolve_token(bad)
