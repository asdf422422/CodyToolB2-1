"""Application exceptions suitable for concise CLI reporting."""


class BudgetAppError(Exception):
    """Base class for expected, user-facing application errors."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ValidationError(BudgetAppError):
    """Raised when user-provided or stored values violate data rules."""


class StorageError(BudgetAppError):
    """Raised when a data file cannot be read or safely written."""


class DataFormatError(StorageError):
    """Raised when a persistent file has an invalid header or record."""


class NotFoundError(BudgetAppError):
    """Raised when a requested application record does not exist."""


class ConflictError(BudgetAppError):
    """Raised when an operation conflicts with existing application data."""
