from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import install_error_handlers
from app.api.exercises import router as exercises_router
from app.api.history_queries import router as history_queries_router
from app.api.workout_events import router as workout_events_router
from app.api.workout_status import router as workout_status_router
from app.api.workouts import router as workouts_router
from app.api.program_events import router as program_events_router
from app.api.web_auth import router as web_auth_router
from app.api.telegram_links import router as telegram_links_router, internal_router as telegram_links_internal_router
from app.api.web_history import router as web_history_router
from app.config import get_settings
from app.infrastructure.database.session import engine
from app.infrastructure.llm import GeminiWorkoutTextInterpreter

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    if settings.llm_provider != "gemini":
        raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")
    if settings.gemini_api_key is None:
        raise RuntimeError("GEMINI_API_KEY is required by the API service")
    interpreter = GeminiWorkoutTextInterpreter(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
        thinking_level=settings.llm_thinking_level,
    )
    application.state.workout_text_interpreter = interpreter
    application.state.exercise_query_interpreter = interpreter
    try:
        yield
    finally:
        await interpreter.close()
        await engine.dispose()


app = FastAPI(title="JIM007 API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def private_web_responses_are_not_cached(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth/", "/api/me/")):
        response.headers["Cache-Control"] = "no-store"
    return response


install_error_handlers(app)
app.include_router(exercises_router)
app.include_router(history_queries_router)
app.include_router(workouts_router)
app.include_router(workout_events_router)
app.include_router(workout_status_router)
app.include_router(program_events_router)
app.include_router(web_auth_router)
app.include_router(telegram_links_router)
app.include_router(telegram_links_internal_router)
app.include_router(web_history_router)


@app.get("/health/live", tags=["system"])
async def liveness() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


@app.get("/health/ready", tags=["system"])
async def readiness() -> dict[str, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from error

    return {"status": "ok", "database": "ready"}


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return await readiness()
