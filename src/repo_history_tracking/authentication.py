"""Credential resolution without account discovery or token logging."""

import os

from .errors import AuthenticationError


def resolve_token(token: str | None = None) -> str:
    if token is not None:
        if not isinstance(token, str) or not token.strip():
            raise AuthenticationError("explicit token must be a nonempty string")
        return token.strip()
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise AuthenticationError("set GH_TOKEN or GITHUB_TOKEN, or supply a token")
