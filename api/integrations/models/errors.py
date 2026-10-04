"""Connector error hierarchy.

Every error carries a machine-readable `kind`. Task handlers catch these and
return {"ok": false, "error": {"type": <kind>, "message": ..., "retry_after": ...}}
so an agent can decide whether to retry, re-authenticate, or explain the failure.
"""


class ConnectorError(Exception):
    kind = "connector_error"  # overridden by subclasses, surfaced as error.type

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.status = status  # upstream HTTP status, when any
        self.retry_after = retry_after  # seconds, when the upstream told us

    def to_dict(self) -> dict:
        err: dict = {"type": self.kind, "message": self.message}
        if self.status is not None:
            err["status"] = self.status
        if self.retry_after is not None:
            err["retry_after"] = self.retry_after
        return err


class ConfigError(ConnectorError):
    """Missing env var, unknown integration, bad integrations_config.json."""

    kind = "config_error"


class AuthError(ConnectorError):
    """401/403, invalid API key, refresh failed. Agent should ask user to reconnect."""

    kind = "auth_error"


class NotFoundError(ConnectorError):
    kind = "not_found"


class RateLimitError(ConnectorError):
    """Upstream rate limit still exceeded after retries. Agent should wait retry_after."""

    kind = "rate_limited"


class UpstreamError(ConnectorError):
    """Any other non-2xx from the vendor API, body redacted."""

    kind = "upstream_error"


class ValidationError(ConnectorError):
    """Bad task inputs (unknown filter field, query too long, ...)."""

    kind = "validation_error"
