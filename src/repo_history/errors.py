"""Public contract errors."""

from datetime import datetime


class ReportValidationError(ValueError):
    def __init__(self, path: str, message: str):
        self.path = path
        super().__init__(f"{path}: {message}")


class ReportWriteError(ValueError):
    """Existing destination reports are invalid or inconsistent."""


class GitHubAPIError(RuntimeError):
    """Safe diagnostics for a failed or incomplete GitHub operation."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        status: int | None = None,
        request_id: str | None = None,
        attempts: int = 0,
        retry_after: float | None = None,
        reset_at: datetime | None = None,
    ) -> None:
        self.operation = operation
        self.status = status
        self.request_id = request_id
        self.attempts = attempts
        self.retry_after = retry_after
        self.reset_at = reset_at
        super().__init__(message)


class AuthenticationError(GitHubAPIError):
    """Missing/invalid credentials or a denied request."""


class TransportError(GitHubAPIError):
    """An HTTP or network failure."""


class GraphQLError(GitHubAPIError):
    """Malformed or failed GraphQL response; partial data is unusable."""


class RateLimitError(GitHubAPIError):
    """Rate budget exhausted; callers must arrange a later run."""


class PaginationError(GitHubAPIError):
    """Malformed connection or cursor progression."""


class RepositoryUnavailableError(GitHubAPIError):
    """Repository unavailable to this credential."""


class QueryResourceError(GraphQLError):
    """Query exceeds server time/resource limits."""
