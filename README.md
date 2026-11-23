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

Everything has defaults. To override (ports, SMTP server, origin), copy `.env.example` to `.env`
and edit it. To send real e-mails, set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`SMTP_USE_TLS` and `MAIL_FROM`.

Data lives in two Docker volumes: `pgdata` (PostgreSQL) and `uploads_data` (uploaded files).

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

## Documentation

- [CLAUDE.md](CLAUDE.md): rules and commands for contributors (and Claude Code)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DATA_MODEL.md](docs/DATA_MODEL.md),
  [docs/API_AND_WS_SPEC.md](docs/API_AND_WS_SPEC.md), [docs/FRONTEND_SPEC.md](docs/FRONTEND_SPEC.md),
  [docs/ROADMAP_AND_DOD.md](docs/ROADMAP_AND_DOD.md)
