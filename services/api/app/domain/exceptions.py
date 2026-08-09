"""Domain and application errors that do not depend on transport concerns."""


class DomainError(Exception):
    """Base error for a violated domain or application rule."""


class NotFoundError(DomainError):
    """A requested entity does not exist or is not owned by the caller."""


class ConflictError(DomainError):
    """The requested operation conflicts with current state."""


class TelegramNotLinkedError(NotFoundError):
    """The Telegram identity is not linked to a web account."""


class InvalidAuthTokenError(DomainError):
    """An authentication token is invalid, expired, or already used."""


class InvalidCredentialsError(DomainError):
    """The supplied login credentials are invalid."""


class EmailNotVerifiedError(DomainError):
    """The account email has not been verified."""


class TelegramLinkNotFoundError(NotFoundError):
    """The Telegram link request does not exist."""


class TelegramLinkInvalidError(DomainError):
    """The Telegram link request is invalid or expired."""


class TelegramAlreadyLinkedError(ConflictError):
    """The Telegram identity is already linked to another account."""


class UserAlreadyHasTelegramError(ConflictError):
    """The web account already has a Telegram connection."""


class ActiveWorkoutExistsError(ConflictError):
    """The user already owns a draft workout."""

    def __init__(self, workout_id: object) -> None:
        self.workout_id = workout_id
        super().__init__(f"An active workout already exists: {workout_id}")


class WorkoutNotEditableError(ConflictError):
    """A completed workout cannot be changed."""


class IdempotencyConflictError(ConflictError):
    """An idempotency key was reused for a different command."""


class ProgramWorkoutConflictError(ConflictError):
    """An active programmed workout already uses the number or alias."""


class InvalidWorkoutStateError(ConflictError):
    """A workout cannot perform the requested state transition."""


class NoActiveWorkoutError(ConflictError):
    """The user has no draft workout to update or complete."""


class NothingToUndoError(ConflictError):
    """The active workout has no logged message to undo."""


class InvalidWorkoutDateError(DomainError):
    """The requested workout date violates tracking rules."""


class InvalidHistoryCursorError(DomainError):
    """A history cursor cannot be decoded or validated."""


class LlmError(Exception):
    """Base error for failures at the text interpretation boundary."""


class LlmUnavailableError(LlmError):
    """The configured LLM provider is temporarily unavailable."""


class LlmTimeoutError(LlmError):
    """The configured LLM provider exceeded its deadline."""


class LlmInvalidResponseError(LlmError):
    """The configured LLM provider returned unusable structured output."""
