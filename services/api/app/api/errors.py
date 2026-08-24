"""Translate technology-independent failures into stable HTTP errors."""

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.domain.exceptions import (
    ActiveWorkoutExistsError,
    ConflictError,
    DomainError,
    ExerciseNameConflictError,
    TelegramNotLinkedError,
    InvalidHistoryCursorError,
    LlmInvalidResponseError,
    LlmTimeoutError,
    LlmUnavailableError,
    NotFoundError,
)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidHistoryCursorError)
    async def invalid_history_cursor(
        _: Request, error: InvalidHistoryCursorError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": {
                    "code": "invalid_history_cursor",
                    "message": str(error),
                }
            },
        )

    @app.exception_handler(LlmInvalidResponseError)
    async def llm_invalid_response(_: Request, error: LlmInvalidResponseError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": {"code": "llm_invalid_response", "message": str(error)}},
        )

    @app.exception_handler(LlmTimeoutError)
    async def llm_timeout(_: Request, error: LlmTimeoutError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={"detail": {"code": "llm_timeout", "message": str(error)}},
        )

    @app.exception_handler(LlmUnavailableError)
    async def llm_unavailable(_: Request, error: LlmUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": {"code": "llm_unavailable", "message": str(error)}},
        )

    @app.exception_handler(TelegramNotLinkedError)
    async def telegram_not_linked(
        _: Request, error: TelegramNotLinkedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "detail": {
                    "code": "telegram_not_linked",
                    "message": str(error),
                }
            },
        )

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": {"code": "not_found", "message": str(error)}},
        )

    @app.exception_handler(ActiveWorkoutExistsError)
    async def active_workout(_: Request, error: ActiveWorkoutExistsError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": {
                    "code": "active_workout_exists",
                    "message": str(error),
                    "workout_id": str(error.workout_id),
                }
            },
        )

    @app.exception_handler(ExerciseNameConflictError)
    async def exercise_name_conflict(
        _: Request, error: ExerciseNameConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": {
                    "code": "exercise_name_conflict",
                    "message": str(error),
                }
            },
        )

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, error: ConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": {
                    "code": error.__class__.__name__.removesuffix("Error").lower(),
                    "message": str(error),
                }
            },
        )

    @app.exception_handler(ValidationError)
    async def domain_validation(_: Request, error: ValidationError) -> JSONResponse:
        details = [
            {
                "type": item["type"],
                "loc": item["loc"],
                "msg": item["msg"],
                "input": item.get("input"),
            }
            for item in error.errors(include_url=False)
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder({"detail": details}),
        )

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": {
                    "code": error.__class__.__name__.removesuffix("Error").lower(),
                    "message": str(error),
                }
            },
        )
