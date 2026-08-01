FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "-m", "app.main"]
