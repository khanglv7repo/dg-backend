from __future__ import annotations


class GovernanceError(Exception):
    """Base typed error for controlled job and API behavior."""

    code = "GOVERNANCE_ERROR"
    retryable = False

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(GovernanceError):
    code = "VALIDATION_ERROR"


class NotFoundError(GovernanceError):
    code = "NOT_FOUND"


class ConflictError(GovernanceError):
    code = "CONFLICT"


class AuthorizationError(GovernanceError):
    code = "FORBIDDEN"


class ConfigurationError(GovernanceError):
    code = "CONFIGURATION_ERROR"


class ExternalSystemError(GovernanceError):
    code = "EXTERNAL_SYSTEM_ERROR"

    def __init__(
        self,
        message: str,
        *,
        system: str,
        retryable: bool = False,
        status_code: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.system = system
        self.retryable = retryable
        self.status_code = status_code
