"""
custom_service_exceptions.py - Service layer exceptions.
"""


class AppException(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class MigrationDoesNotExist(AppException):
    def __init__(self, detail: str = "Migration does not exist"):
        super().__init__(detail)


class MigrationAlreadyExists(AppException):
    def __init__(self, detail: str = "Migration already exists"):
        super().__init__(detail)


class MigrationInvalidState(AppException):
    def __init__(self, detail: str = "Migration is in an invalid state for this operation"):
        super().__init__(detail)


class AgentExecutionError(AppException):
    def __init__(self, detail: str = "Agent execution failed"):
        super().__init__(detail)


class ServiceDBError(AppException):
    def __init__(self, detail: str = "Database error"):
        super().__init__(detail)


class MissingCredentialsError(AppException):
    """Raised when credentials_pre_validated=True but a required provider is absent from Vault."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        detail = f"Missing credentials for provider(s): {', '.join(missing)}"
        super().__init__(detail)
