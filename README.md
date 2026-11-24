# Online Chat Server

A classic web chat: public and private rooms, contacts and direct messages, file attachments,
online / AFK / offline presence and room moderation.

## Run

The only requirement on the host is Docker (with Compose v2).

```bash
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:8080 | The application |
| http://localhost:8080/api/docs | OpenAPI (Swagger UI) |
| http://localhost:8025 | Mailpit: catches password reset e-mails in development |

Database migrations run automatically when the backend starts. No demo data is created.

```bash
docker compose down        # stop, keep data
docker compose down -v     # stop and delete all data (database and uploaded files)
```

## Configuration

Everything has defaults. To override, copy `.env.example` to `.env`, edit it and run
`docker compose up -d` again.

| Variable | Default | Meaning |
|---|---|---|
| `APP_PORT` | `8080` | Host port of the application |
| `MAILPIT_UI_PORT` | `8025` | Host port of the Mailpit UI |
| `APP_ORIGIN` | `http://localhost:8080` | Browser origin(s) allowed to call the API and open WebSockets, comma separated. The first one is used in e-mail links |
| `SMTP_HOST` / `SMTP_PORT` | `mailpit` / `1025` | SMTP server for password reset e-mails |
| `SMTP_USER` / `SMTP_PASSWORD` | — | SMTP credentials, if the server needs them |
| `SMTP_USE_TLS` | `false` | Implicit TLS (e.g. port 465) |
| `MAIL_FROM` | `Chat <no-reply@chat.local>` | Sender of the e-mails |
| `SESSION_TTL_DAYS` | `30` | Lifetime of "Keep me signed in" sessions |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `chat` | Applied when the `pgdata` volume is first created |

**Opening the app from other devices** (LAN): every origin the browser uses must be listed in
`APP_ORIGIN`, e.g. `APP_ORIGIN=http://localhost:8080,http://192.168.1.20:8080`; requests and
WebSockets from any other origin are refused. Under WSL2 the port also has to be forwarded from
Windows (`netsh interface portproxy` + a firewall rule, or `networkingMode=mirrored`).

Data lives in two Docker volumes: `pgdata` (PostgreSQL) and `uploads_data` (uploaded files).

## Architecture in brief

```
browser ──HTTP / WS──▶ frontend (nginx: React build, proxies /api and /ws)
                          └──▶ backend (FastAPI, one uvicorn process) ──▶ PostgreSQL 16
                                    │                                 └──▶ SMTP (Mailpit in dev)
                                    └── /data/uploads (volume, served only through the API)
```

- **REST** for everything that changes state; **one WebSocket per tab** for live events
  (messages, typing, presence, membership, unread counters).
- The WebSocket manager lives in the backend process: per-connection send queues, chat
  subscriptions, presence aggregated over all tabs and sessions of a user
  (online → AFK after 60 s without activity → offline when the last tab closes). That is why the
  backend runs a single worker; scaling out would need a pub/sub (docs/ARCHITECTURE.md §4.7).
- Permission checks live in one module (`backend/app/services/access.py`); files are never served
  statically, every download re-checks that the user is *currently* allowed to see the chat.
- Messages are ordered by id and paged with cursors only; the feed is virtualised, so long
  histories stay smooth.

## Tests

```bash
docker compose exec backend pytest -q                 # backend (needs the stack running)
docker compose run --rm backend pytest -q             # backend, stack not running
docker run --rm -v "$PWD/frontend":/app -w /app node:20-alpine \
  sh -c "npm ci && npm run typecheck && npm test -- --run"   # frontend
```

Backend tests use a separate `chat_test` database, created when the `pgdata` volume is first
initialised. If your volume predates `db/init/`, create it once:
`docker compose exec db createdb -U chat chat_test`.

## Load test

```bash
docker compose exec backend python -m tests.load.run --users 300 --rooms 20
```

Creates 300 users with sessions (plus 700 idle ones to fill a 1000-member room) and 20 rooms
directly in the database, opens 300 WebSockets, sends messages for 60 s while repeatedly
disconnecting and reconnecting random users, then deletes everything it created. It fails unless
p95 delivery < 3 s, p95 presence < 2 s and nothing was lost. A run on a developer laptop
(the load client shares the backend container):

| Metric | Result |
|---|---|
| Messages / deliveries | 1 801 / 184 807, none lost, no errors |
| Delivery latency | p50 47 ms, p95 196 ms, p99 399 ms (first run on a fresh stack) |
| Presence latency | p50 594 ms, p95 1 030 ms (offline includes the 1 s reload grace period) |

`--help` lists the knobs (duration, message rate, big room size, `--keep`).

## Documentation

- [CLAUDE.md](CLAUDE.md): rules and commands for contributors (and Claude Code)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DATA_MODEL.md](docs/DATA_MODEL.md),
  [docs/API_AND_WS_SPEC.md](docs/API_AND_WS_SPEC.md), [docs/FRONTEND_SPEC.md](docs/FRONTEND_SPEC.md),
  [docs/ROADMAP_AND_DOD.md](docs/ROADMAP_AND_DOD.md),
  [docs/MANUAL_TEST_CHECKLIST.md](docs/MANUAL_TEST_CHECKLIST.md)
