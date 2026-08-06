"""Domain and application errors that do not depend on transport concerns."""


class DomainError(Exception):
    """Base error for a violated domain or application rule."""


class NotFoundError(DomainError):
    """A requested entity does not exist or is not owned by the caller."""


class ExternalIdentityNotRegisteredError(NotFoundError):
    """A chat provider identity has not completed registration."""


class ConflictError(DomainError):
    """The requested operation conflicts with current state."""


class ActiveWorkoutExistsError(ConflictError):
    """The user already owns a draft workout."""

    def __init__(self, workout_id: object) -> None:
        self.workout_id = workout_id
        super().__init__(f"An active workout already exists: {workout_id}")


class WorkoutNotEditableError(ConflictError):
    """A completed workout cannot be changed."""


class IdempotencyConflictError(ConflictError):
    """An idempotency key was reused for a different command."""


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
