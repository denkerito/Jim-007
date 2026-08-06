"""Read the active workout for a provider-neutral external identity."""

from app.application.commands import GetWorkoutStatusCommand, WorkoutStatusResult
from app.application.ports import UnitOfWorkFactory
from app.domain.exceptions import ExternalIdentityNotRegisteredError


class GetWorkoutStatus:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: GetWorkoutStatusCommand) -> WorkoutStatusResult:
        async with self._uow_factory() as uow:
            identity = await uow.external_identities.get_by_provider_subject(
                command.provider.strip(), command.provider_subject.strip()
            )
            if identity is None:
                raise ExternalIdentityNotRegisteredError(
                    "External identity is not registered"
                )
            workout = await uow.workouts.get_active_draft(identity.user_id)
            if workout is None:
                return WorkoutStatusResult(kind="none")
            return WorkoutStatusResult(kind="active", workout=workout)
