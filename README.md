# JIM007

**A conversational workout tracker that turns natural language into reliable, structured training data.**

```text
You:  bench press
Bot:  How many sets and reps, and with what load?
You:  55x8 55x6 55x6

✓ Bench press — 55 kg × 8, 55 kg × 6, 55 kg × 6
```

JIM007 combines a web application with a Telegram assistant. Athletes can log workouts as they naturally describe them, while the backend converts each message into validated, queryable data.

The LLM is deliberately kept outside the database boundary: it interprets text into a strict schema, and the application validates ownership, workout state, exercise references, and idempotency before anything is persisted.

## Why this project is different

- **One-shot conversational clarification** — ambiguous input triggers one concise question for all missing information. The next message either resolves the log or asks the user to rewrite it completely.
- **Reliable AI orchestration** — structured outputs, versioned prompts, strict validation, bounded retries, provider timeouts, and no database transaction held during an LLM call.
- **Exactly-once persistence** — idempotency keys and transactional writes prevent duplicated exercises and sets, including concurrent retries.
- **Privacy-aware state** — clarification text expires after 15 minutes and is deleted as soon as the flow reaches a terminal state.
- **Clear service boundaries** — the Telegram bot is a thin client; business rules, persistence, and AI orchestration live in the backend.
- **Contract-tested integration** — versioned OpenAPI snapshots and provider/consumer tests protect the internal API shared by the bot and backend.

## Features

- Natural-language workout logging, including multiple exercises per message
- Compact set notation such as `80x8 80x8 80x7` and `70x10x3`
- Conversational recovery from incomplete workout logs
- Draft workouts with status, undo, completion, and cancellation
- Workout and per-exercise history
- Reusable training programs with aliases
- Web registration, email verification, and secure Telegram account linking
- Searchable exercise directory and recent activity dashboard

## Architecture

```text
Browser ──> Nginx ──> React
                       │
                       v
Telegram ──> Bot ──> FastAPI ──> PostgreSQL
                         │
                         └──────> Gemini API
```

The repository contains three independently deployable services:

- `services/api` — FastAPI, application and domain logic, PostgreSQL access, Alembic migrations, and the Gemini adapter
- `services/telegram-bot` — a stateless Telegram adapter using the authenticated internal API
- `services/web` — a React and TypeScript account and workout-history application served by Nginx

The backend follows layered boundaries between `domain`, `application`, `infrastructure`, and `api`. The bot never connects directly to PostgreSQL or the LLM provider.

## Tech stack

- **Backend:** Python 3.13, FastAPI, Pydantic, SQLAlchemy 2.0, Alembic, PostgreSQL 17
- **AI:** Google Gemini, structured outputs, versioned prompts
- **Clients:** React 19, TypeScript, TanStack Query, Tailwind, python-telegram-bot
- **Quality:** pytest, Testcontainers, Vitest, provider/consumer contract tests
- **Infrastructure:** Docker Compose, Nginx, Mailpit

## Run locally

### Requirements

- Docker with Docker Compose
- A Gemini API key
- A Telegram bot token

### Setup

```bash
git clone https://github.com/denkerito/Jim-007.git
cd Jim-007
cp .env.example .env
```

On PowerShell, replace the copy command with:

```powershell
Copy-Item .env.example .env
```

Before starting the stack, configure the required secrets in `.env`:

```dotenv
POSTGRES_PASSWORD=choose-a-strong-password
INTERNAL_API_TOKEN=choose-a-long-random-token
SESSION_SECRET=choose-an-independent-long-random-secret
CSRF_SECRET=choose-a-different-long-random-secret
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_BOT_USERNAME=your-bot-username-without-at
GEMINI_API_KEY=your-gemini-api-key
```

Start the complete stack:

```bash
docker compose up --build
```

Once running:

- Web app: `http://127.0.0.1:3000`
- API documentation: `http://127.0.0.1:8000/docs`
- Development email: `http://127.0.0.1:8025`

Compose starts PostgreSQL, applies all migrations, and launches the API, web app, Telegram bot, and Mailpit.

## Try the conversation

Register in the web app, verify the account through Mailpit, and link Telegram from the account page. Then open the bot:

```text
/workout today
bench press 80x8 80x8 80x7
pull-ups 10x3
lat pulldown
# Bot asks once for every missing value
70x10x3
/status
/end
/history
```

Workouts remain drafts and are excluded from history until `/end` completes them.

## Test

Each service owns its dependencies and test suite.

```bash
# API — integration tests require Docker
cd services/api
python -m pip install -e ".[test]"
pytest

# Telegram bot
cd ../telegram-bot
python -m pip install -e ".[test]"
pytest

# Web
cd ../web
pnpm install
pnpm typecheck
pnpm lint
pnpm test
pnpm build
```

The active internal API contract is stored in `contracts/internal-api/v2` and verified from both the provider and consumer sides with `pytest -m contract`.

## Repository

```text
.
├── contracts/internal-api/   # Versioned bot/API contracts
├── docs/                     # Architecture and domain decisions
├── services/
│   ├── api/                  # Backend, AI orchestration, database
│   ├── telegram-bot/         # Stateless conversational client
│   └── web/                  # React application
├── .env.example
└── docker-compose.yaml
```

More detail is available in the [architecture](docs/architecture.md), [database model](docs/database.md), and [product vision](docs/vision.md).

## Roadmap

- Web workout creation and editing
- Progress charts and training statistics
- RPE, RIR, duration, and distance metrics
- Conversational corrections with an audit trail
- Canonical exercise aliases and smarter matching

## License

No license has been added yet. The repository remains under standard copyright restrictions.
