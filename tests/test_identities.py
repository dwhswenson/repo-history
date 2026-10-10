"""Tests for repo_history.identities."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from repo_history import (
    build_report,
    git_identity_key,
    unresolved_git_actor,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "email,name,kind,value",
    [
        (" Example@Email.org ", "ignored", "email", "example@email.org"),
        (None, " Name Only ", "name", "Name Only"),
        ("  ", "Name Only", "name", "Name Only"),
    ],
)
def test_identity_hash(email, name, kind, value):
    key = f"git:{kind}:{sha256(value.encode()).hexdigest()}"
    assert git_identity_key(email, name) == key
    actor = unresolved_git_actor(email, name)
    assert actor.key == key
    assert actor.git_email == email
    assert actor.git_name == name
    assert actor.classification == "unresolved_git"


def test_identity_namespaces_and_missing():
    assert git_identity_key("Same", None) != git_identity_key(None, "Same")
    assert git_identity_key(None, "Name") != git_identity_key(None, "name")
    assert git_identity_key("EMAIL", None) == git_identity_key("email", None)
    assert unresolved_git_actor(" ", None) is None
    assert git_identity_key(None, " ") is None
    assert unresolved_git_actor(None, "tool[bot]").classification == "unresolved_git"


def test_linked_identity_survives_login_change(mixed):
    actors = [
        replace(a, login="new-login") if a.key == "U_author" else a
        for a in mixed.actors
    ]
    report = build_report(mixed.metadata, actors, mixed.activities)
    assert report.summaries == mixed.summaries
