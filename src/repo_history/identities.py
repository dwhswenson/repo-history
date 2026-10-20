"""Deterministic estimated identities; never infer linked accounts or bot status."""

from hashlib import sha256

from .models import Actor


def git_identity_key(email: str | None, name: str | None) -> str | None:
    """Prefer trimmed casefolded email, then trimmed case-preserved name."""
    if email and email.strip():
        kind, value = "email", email.strip().casefold()
    elif name and name.strip():
        kind, value = "name", name.strip()
    else:
        return None
    return f"git:{kind}:{sha256(value.encode('utf-8')).hexdigest()}"


def unlinked_git_actor(email: str | None, name: str | None) -> Actor | None:
    key = git_identity_key(email, name)
    if key is None:
        return None
    return Actor(key, "unlinked_git", git_name=name, git_email=email)
