from app.api.schemas import WorkoutEventRequest
from app.application.commands import ProcessWorkoutEventCommand


def test_workout_event_identity_fields_preserve_whitespace_only_inputs() -> None:
    request = WorkoutEventRequest(
        provider=" ",
        provider_subject=" ",
        action="open",
    )
    command = ProcessWorkoutEventCommand(
        provider=request.provider,
        provider_subject=request.provider_subject,
        action=request.action,
        text=request.text,
        idempotency_key="telegram:update:1",
        request_hash="a" * 64,
    )

    assert command.provider == " "
    assert command.provider_subject == " "
