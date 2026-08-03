from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import install_error_handlers
from app.api.workouts import router as workouts_router
from app.config import get_settings
from app.infrastructure.database.session import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    del application
    yield
    await engine.dispose()


app = FastAPI(title="JIM007 API", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
app.include_router(workouts_router)


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
