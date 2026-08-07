# JIM007

JIM007 is a Telegram-based workout tracker that turns natural-language messages into structured training data.

Instead of filling in forms during a workout, users can write messages such as `bench press 80x8 80x8 80x7`. The backend interprets the message through an LLM, validates the result, and stores it in PostgreSQL.

## Features

- Telegram registration and workout logging
- Draft workouts with status, undo, cancel, and explicit completion
- Workout and exercise history
- Reusable training-day programs
- AI-assisted interpretation with application-side validation
- Versioned contract between the Telegram bot and the API

## Architecture

```text
Telegram -> Telegram Bot -> FastAPI -> PostgreSQL
                              |
                              +-> Gemini API
```

The repository is a small Python monorepo with two independent services:

- `services/api`: FastAPI backend, domain logic, persistence, and LLM integration
- `services/telegram-bot`: thin Telegram adapter that communicates with the backend over HTTP

The backend follows a layered architecture and keeps domain logic independent from Telegram, the database, and the selected LLM provider.

## Tech stack

Python 3.13, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Pydantic, python-telegram-bot, Gemini, Docker Compose, and pytest.

## Run locally

Requirements: Docker with Docker Compose, a Telegram bot token, and a Gemini API key.

```bash
cp .env.example .env
# Add your credentials to .env
docker compose up --build
```

The API is available at `http://localhost:8000`. Database migrations run automatically before the API starts.

## Documentation

More details are available in [`docs/architecture.md`](docs/architecture.md) and [`docs/vision.md`](docs/vision.md).
