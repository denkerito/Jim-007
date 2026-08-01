from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

app = FastAPI(title="JIM007 API", version="0.1.0")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}
