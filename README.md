# JIM007

**A conversational workout tracker.**

JIM007 turns natural-language training notes into structured workout data. Open a workout in Telegram, send something like `bench press 80x8 80x8 80x7`, and the application uses LLM to interpret the message before validating and storing it in PostgreSQL.

The LLM is only an interpreter: it never accesses the database, and every generated payload is checked by the application before it is persisted.

> **Project status:** web-first authentication and secure Telegram linking are implemented. The progress/history dashboard remains a later iteration.

## Features

- Register and verify a web account with email and password
- Link exactly one Telegram account through an expiring, two-step confirmation flow
- Log one or more exercises from natural-language messages
- Keep workouts as drafts until they are explicitly completed
- Inspect, undo the last entry, or cancel an active workout
- Browse workout and per-exercise history
- Create reusable training-day programs with prescribed sets, reps, and rest
- Resolve dates, exercise names, and program aliases conversationally
- Prevent duplicate writes with idempotent commands
- Keep the Telegram adapter, business logic, persistence, and LLM integration loosely coupled
- Version and test the internal HTTP contract shared by the bot and API

## How it works

```text
Browser ---- Nginx ----> FastAPI ----> PostgreSQL
                           ^  |
                           |  +---------> SMTP / Gemini API
Telegram ---- Bot ----------+
```

The repository contains three independently deployable application services:

- `services/telegram-bot` — a thin Telegram adapter built with `python-telegram-bot`
- `services/api` — the FastAPI backend, domain logic, database access, migrations, and Gemini adapter
- `services/web` — the Vite/React account application, served by Nginx

The backend uses layered boundaries (`domain`, `application`, `infrastructure`, and `api`). The bot communicates with it through an authenticated internal API; it does not connect to PostgreSQL or Gemini directly.

## Tech stack

- Python 3.13
- FastAPI and Pydantic
- SQLAlchemy 2.0, Alembic, and PostgreSQL 17
- `python-telegram-bot`
- Google Gemini Developer API
- Docker Compose
- pytest and Testcontainers
- React 19, TypeScript, TanStack Query, Tailwind and Vitest

## Getting started

### Prerequisites

- Docker with Docker Compose
- A Telegram bot token
- A Gemini API key

### 1. Configure the environment

Clone the repository and create a local environment file:

```bash
git clone https://github.com/denkerito/Jim-007.git
cd Jim-007
cp .env.example .env
```

On PowerShell, use `Copy-Item .env.example .env` instead of `cp`.

Set at least these values in `.env`:

```dotenv
POSTGRES_PASSWORD=choose-a-strong-password
INTERNAL_API_TOKEN=choose-a-long-random-token
SESSION_SECRET=choose-an-independent-long-random-secret
CSRF_SECRET=choose-a-different-long-random-secret
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_BOT_USERNAME=your-bot-username-without-at
GEMINI_API_KEY=your-gemini-api-key
```

Do not commit `.env`; it contains secrets and is ignored by Git.

### 2. Start the application

```bash
docker compose up --build
```

Compose starts PostgreSQL, applies all Alembic migrations, starts Mailpit and the API, then starts the web app and bot.

Open the application at `http://127.0.0.1:3000`. Development email is available in Mailpit at `http://127.0.0.1:8025`.

The API is bound to `http://127.0.0.1:8000` by default:

- Swagger UI: `http://127.0.0.1:8000/docs`
- Liveness: `http://127.0.0.1:8000/health/live`
- Readiness: `http://127.0.0.1:8000/health/ready`

Change `API_PORT` in `.env` if port 8000 is already in use.

### Upgrading an existing development database

Rows created by the former Telegram-first flow do not have a web credential and
are deliberately not converted into placeholder accounts. For this development
rollout the existing PostgreSQL data is considered disposable.

After stopping Compose, first inspect the exact project volume:

```bash
docker compose down
docker volume inspect jim007_postgres_data
```

Only after confirming that this is the disposable JIM007 development volume,
remove that exact volume and start the stack again:

```bash
docker volume rm jim007_postgres_data
docker compose up --build
```

This deletion is irreversible. It is not performed by the migration or by any
application startup command.

### 3. Create an account and link Telegram

Register in the web app, open the verification link from Mailpit, then use **Collega Telegram** on `/account`. The bot records only a candidate identity; the connection becomes active only after explicit confirmation in the browser.

Plain `/start` never creates an account. An unlinked user is sent back to the web app. After linking, try this flow:

Open a private chat with your bot and try this flow:

```text
/start
/workout today
bench press 80x8 80x8 80x7
lat pulldown 70x10x3
/status
/end
/history
```

An active workout remains a draft and is excluded from history until `/end` completes it.

## Bot commands

| Command | Purpose |
| --- | --- |
| `/start` | Resolve the current connection or accept a web-generated link token |
| `/workout [date or program]` | Open a workout for a natural-language date or saved training day |
| `/status` | Show the complete active draft |
| `/undo` | Remove everything added by the most recent workout message |
| `/end` | Complete the active workout |
| `/cancel` | Permanently delete the active draft |
| `/history [limit]` | Show recent completed workouts; limit defaults to 5 and cannot exceed 20 |
| `/exercise <name> [limit]` | Show history for an exercise |
| `/program <number> <alias>, <plan>[, notes]` | Save a reusable training day |
| `/editprogram <number or alias>, <plan>[, notes]` | Replace a training day with a new version |
| `/newprogram` | Deactivate the current program days |
| `/help` | Show the command summary |

Workout text can contain multiple exercises. Compact notation such as `70x10x3` is expanded to three sets of 10 reps at 70 kg. Ambiguous input produces a clarification request instead of a database write.

## Development and tests

Each service has its own package metadata and test suite. Create separate virtual environments to avoid coupling their dependencies.

### API

```bash
cd services/api
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
pytest
```

The integration tests use Testcontainers and require a running Docker daemon. The optional live Gemini smoke test only runs when its explicit environment flag and a real API key are provided.

### Telegram bot

```bash
cd services/telegram-bot
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
pytest
```

### Web app

```bash
cd services/web
pnpm install
pnpm typecheck
pnpm lint
pnpm test
pnpm build
```

On Windows, activate a virtual environment with `.venv\\Scripts\\Activate.ps1`.

### Internal API contract

The active bot-facing OpenAPI snapshot and interaction examples live in `contracts/internal-api/v2`; `v1` remains archived. Check both provider and consumer contracts with:

```bash
cd services/api
pytest -m contract

cd ../telegram-bot
pytest -m contract
```

After an intentional internal API change, regenerate the snapshot from `services/api` and review the resulting diff:

```bash
python scripts/internal_api_contract.py --write
```

## Repository layout

```text
.
|-- contracts/internal-api/v2/  # Active bot/API contract
|-- docs/                       # Architecture, domain, and product notes
|-- services/
|   |-- api/                    # FastAPI application and Alembic migrations
|   `-- telegram-bot/           # Telegram handlers and backend client
|-- .env.example                # Local configuration template
`-- docker-compose.yaml         # Local application stack
```

## Documentation

- [Architecture](docs/architecture.md)
- [Database and domain model](docs/database.md)
- [Product vision](docs/vision.md)
- [Internal API contract](contracts/internal-api/v2/README.md)

## Roadmap

- Web dashboard for workout history, progression, and statistics
- Richer exercise metrics such as duration, distance, RPE, and RIR
- Conversational correction and audit history
- Exercise aliases and a canonical exercise catalog
- Web authentication and account linking

## License

No license has been added yet. Until one is provided, the repository remains under standard copyright restrictions.
