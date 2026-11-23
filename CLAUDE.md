# CLAUDE.md — Online Chat Server

Классический веб-чат: публичные/приватные комнаты, контакты и личные сообщения (DM), вложения, presence (`online` / `AFK` / `offline`), модерация. Источник истины по требованиям — ТЗ «Online Chat Server — Requirements» (разделы 1–7, Appendix A). Детальные спецификации:

| Документ | О чём |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Контейнеры, WebSocket-менеджер, presence, файловое хранилище |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Схема PostgreSQL, индексы, матрица каскадных удалений |
| [docs/API_AND_WS_SPEC.md](docs/API_AND_WS_SPEC.md) | REST-эндпоинты, коды ошибок, WebSocket-протокол |
| [docs/FRONTEND_SPEC.md](docs/FRONTEND_SPEC.md) | Экраны, компоненты, поведение UI |
| [docs/ROADMAP_AND_DOD.md](docs/ROADMAP_AND_DOD.md) | Этапы разработки, Definition of Done |
| [docs/MANUAL_TEST_CHECKLIST.md](docs/MANUAL_TEST_CHECKLIST.md) | Ручная проверка в браузере (дополняется на каждом этапе) |

**Перед реализацией любой фичи прочитай соответствующий раздел спецификаций. Если код расходится со спецификацией — сначала обнови спецификацию (в том же коммите), потом код.**

---

## 1. Фиксированный стек (не менять без явного решения пользователя)

| Слой | Технологии |
|---|---|
| Frontend | React 18 + Vite + TypeScript (strict), react-router, TanStack Query (REST-кэш), Zustand (WS/presence/unread state), react-virtuoso (лента сообщений), @emoji-mart/react; в проде — статика в nginx |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.x (async, asyncpg), Alembic, Pydantic v2, нативные WebSockets FastAPI/Starlette, argon2-cffi (Argon2id), aiosmtplib, Pillow (проверка изображений), user-agents |
| DB | PostgreSQL 16 (расширения `citext`, `pg_trgm`) |
| Mail (dev) | Mailpit (SMTP `mailpit:1025`, UI http://localhost:8025) |
| Тесты | pytest, pytest-asyncio, httpx (`AsyncClient` + `ASGITransport`), websockets; фронт — vitest + @testing-library/react |
| Запуск | Docker Compose (`compose.yaml` в корне). На хосте нужен только Docker |

## 2. Структура репозитория

```
compose.yaml            .env.example           CLAUDE.md
backend/                Dockerfile, pyproject.toml, alembic.ini, alembic/, app/, tests/
  app/main.py           FastAPI app, lifespan (старт WS-менеджера, presence-тикера, GC файлов)
  app/core/             config (pydantic-settings), security, errors, deps
  app/db/               engine, session, models/ (по одному модулю на агрегат)
  app/api/              routers: auth, sessions, users, contacts, rooms, moderation, messages, attachments, dms
  app/services/         бизнес-логика (НЕ в роутерах): access.py, rooms.py, messages.py, files.py, presence.py ...
  app/ws/               manager.py, protocol.py (pydantic-схемы событий), handlers.py
  tests/                unit/, api/, ws/, load/
frontend/               Dockerfile (multi-stage → nginx), nginx.conf, src/
db/init/                01-create-test-db.sql (создаёт БД chat_test для тестов)
docs/                   спецификации
```

## 3. Команды

```bash
# Сборка и запуск всего стека (миграции применяются автоматически при старте backend)
docker compose up --build            # http://localhost:8080 — приложение, http://localhost:8025 — Mailpit
docker compose up                    # без пересборки
docker compose down                  # остановка, данные сохраняются
docker compose down -v               # остановка + УДАЛЕНИЕ volumes (pgdata, uploads_data) — полный сброс

# Backend-тесты (внутри контейнера, используют отдельную БД chat_test)
docker compose exec backend pytest -q
docker compose exec backend pytest tests/ws -q -k presence
docker compose run --rm backend pytest -q          # если стек не запущен (db поднимется по depends_on)

# Миграции
docker compose exec backend alembic revision --autogenerate -m "add_x"   # затем ОБЯЗАТЕЛЬНО проверить файл руками
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade -1

# Линтеры/типы/тесты по ТЕКУЩИМ исходникам с хоста (образ содержит копию кода на момент сборки;
# `exec backend ...` проверяет именно её — после правок нужен `docker compose up --build -d`)
docker compose run --rm --no-deps -u "$(id -u):$(id -g)" -v "$PWD/backend:/app" \
  -e RUFF_CACHE_DIR=/tmp/ruff -e MYPY_CACHE_DIR=/tmp/mypy backend \
  sh -c "ruff check app tests alembic && ruff format --check app tests alembic && mypy app && pytest -q -p no:cacheprovider"

# Frontend-тесты и типы (без node на хосте)
docker run --rm -v "$PWD/frontend":/app -w /app node:20-alpine sh -c "npm ci && npm run typecheck && npm test -- --run"

# Нагрузочный тест базового продукта (300 WS-клиентов, задержки доставки/presence)
docker compose exec backend python -m tests.load.run --users 300 --rooms 20

# Логи
docker compose logs -f backend
```

## 4. Доменные инварианты (нарушение = баг)

### Лимиты
- Текст сообщения: UTF-8, **≤ 3072 байт** (считаем `len(text.encode("utf-8"))`, не символы). Превышение → `413 message_too_large` (REST) / `error{code:"message_too_large"}` (WS). Пустое сообщение без вложений → `400`.
- Изображение: **≤ 3 МБ** (3 145 728 байт) → иначе `413 image_too_large`. Файл: **≤ 20 МБ** (20 971 520 байт) → иначе `413 file_too_large`. Размер проверяется **потоково** при чтении тела (не доверять `Content-Length`), загрузка прерывается при превышении. nginx `client_max_body_size 21m`.
- «Изображение» определяется по содержимому (Pillow + magic bytes: PNG/JPEG/GIF/WEBP), не по расширению/заявленному MIME.

### Аккаунты и сессии
- `email` и `username` уникальны (регистронезависимо, `citext`). **`username` неизменяем** — нет ни одного эндпоинта/поля, позволяющего его изменить; PATCH профиля с `username` → `400`.
- Пароль хешируется только Argon2id. Верификации email нет.
- Сессия = строка в `user_sessions`; в cookie `chat_session` (HttpOnly, SameSite=Lax) лежит случайный токен, в БД — только его SHA-256.
- «Keep me signed in»: persistent cookie (Max-Age 30 дней, скользящее продление). Без галочки: session-cookie (умирает при закрытии браузера) + серверный TTL 24 ч. Автологаута по неактивности нет.
- **Sign out завершает только текущую сессию** (удаляет её строку, закрывает только её WS-соединения). Другие сессии/браузеры не затрагиваются.
- Завершение сессии с экрана Sessions мгновенно закрывает WS этой сессии (`session_revoked`, close code 4001).
- Сброс пароля: одноразовый токен (SHA-256 в БД, TTL 1 ч), письмо через SMTP (Mailpit в dev). `forgot-password` всегда отвечает `202` (нет перечисления email). Успешный сброс завершает **все** сессии пользователя.

### Presence (multi-tab / multi-session)
- Состояние считается **на сервере** по всем WS-соединениям пользователя (все вкладки × все сессии).
- `online` — есть хотя бы одно соединение с активностью за последние **60 с**.
- `AFK` — соединения есть, но ни в одной вкладке не было активности **> 60 с**.
- `offline` — нет ни одного открытого соединения.
- Изменение статуса рассылается заинтересованным (друзья + участники общих комнат) **< 2 с**. Presence-тикер — раз в 1 с. Сообщения доставляются **< 3 с**.
- Backend работает **одним процессом uvicorn** (`--workers 1`): состояние WS/presence живёт в памяти. Не добавлять воркеры без внедрения pub/sub (см. ARCHITECTURE.md §4.7).

### Контакты, user-ban, DM
- Дружба требует подтверждения получателем. Удаление друга — в любой момент любой стороной.
- DM разрешена **только** если пользователи друзья **И** ни один не забанил другого. Проверка — на каждую отправку/редактирование/удаление.
- User-ban: дружба удаляется автоматически, pending-заявки удаляются, новые сообщения/заявки/приглашения в комнаты от забаненного забанившему запрещены (`403`). История DM **остаётся видимой обоим, но заморожена (read-only)**: нельзя отправлять, редактировать, удалять. Обоим уходит `dm_frozen`.
- DM функционально = комната из 2 фиксированных участников без админов.

### Комнаты и модерация
- `rooms.name` уникально (регистронезависимо). Visibility: `public` (в каталоге) / `private` (только по приглашению; для не-участников — `404`, не `403`).
- Owner ровно один, всегда участник с `is_admin = true`, не может потерять админство, **не может выйти** (только удалить комнату).
- Admin: удаляет любые сообщения, банит/разбанивает, видит бан-лист (кто/когда), снимает админство с других админов (не с owner). Назначать админов и удалять комнату — только owner. Настройки комнаты (name/description/visibility) — только owner.
- **Любое удаление участника админом = бан** (запись в `room_bans`). Забаненный не может вернуться даже в публичную комнату и не может быть приглашён, пока не разбанен.
- Потеря доступа (выход/бан/удаление комнаты) → немедленный запрет на сообщения/файлы этой комнаты через API и UI (`room_access_revoked`). Загруженные им файлы **остаются** в комнате.

### Сообщения
- Редактировать — только автор (`edited_at` → в UI серая пометка `edited`). Удалять — автор (комната/DM) или admin/owner (только комната). Удаление мягкое: контент стирается, вложения удаляются с диска, в ленте плейсхолдер «Message deleted».
- Порядок — по `messages.id` (BIGINT identity). Пагинация **только курсорная** (`before_id`/`after_id`), никакого OFFSET.
- Ответ (`reply_to_message_id`) — только на сообщение из **того же** чата.

### Файлы — безопасность и удаление
- Файлы лежат в volume `uploads_data` (`/data/uploads`), имя на диске — UUID без пользовательских данных. **Никогда не раздавать `/data/uploads` статикой** (ни nginx, ни `StaticFiles`). Только `GET /api/attachments/{id}/download` с проверкой, что пользователь **сейчас** участник комнаты / сторона DM.
- Удаление комнаты: в одной транзакции удаляются строки БД, после commit — каталог `/data/uploads/rooms/{room_id}/` целиком. Удаление аккаунта: удаляются **только комнаты, где он owner** (+ их файлы), членства в чужих комнатах; его сообщения/файлы в чужих комнатах остаются (автор → «Deleted user»). DM-истории с удалённым пользователем остаются у собеседника в замороженном виде.
- Фоновый GC (раз в час): удаляет файлы на диске без строки в БД и pending-вложения старше 24 ч.

### Безопасность (общие правила)
- Все изменяющие запросы проверяют `Origin` (совпадает с `APP_ORIGIN`); WS-handshake тоже проверяет `Origin`.
- Проверки прав — только в `app/services/access.py`; роутеры не содержат ad-hoc проверок.
- Ошибки — единый формат `{"error": {"code": "...", "message": "...", "details": {}}}`. Валидация Pydantic маппится в `400 validation_error` (не 422).
- Никаких seed/демо-данных в миграциях и при старте.

## 5. Правила работы для Claude Code

1. Работай **вертикальными слайсами** по [docs/ROADMAP_AND_DOD.md](docs/ROADMAP_AND_DOD.md): миграция → модели → сервис → API/WS → тесты → UI. Этап закрыт, только когда его чек-лист DoD зелёный и `docker compose up --build` с чистыми volumes работает.
2. Каждый новый эндпоинт/WS-событие — минимум один тест на успех и по тесту на каждый код ошибки из спецификации (особенно `403` по правам и `413` по лимитам).
3. Миграции Alembic: одна логическая миграция на изменение, autogenerate всегда вычитывать руками (индексы с `WHERE`, `citext`, CHECK-ограничения автогенерация теряет). Миграции не редактировать после коммита.
4. Async везде: никаких синхронных вызовов БД/файлов в event loop (файлы — через `anyio.to_thread` / `aiofiles`).
5. Не добавлять зависимости вне стека без вопроса пользователю.
6. Коммит в конце каждого этапа (Conventional Commits: `feat(rooms): ...`), затем рекомендовать `/clear`.

## 6. Изоляция Section 6 (Advanced: Jabber/XMPP, федерация)

**Не трогать XMPP, федерацию, нагрузочный тест федерации и админ-дашборд Jabber до тех пор, пока этапы 1–9 не завершены и весь DoD базового продукта (разделы 1–5, 7, Appendix A) не отмечен как выполненный.** Не создавать заготовок, сервисов в `compose.yaml`, таблиц или флагов под XMPP заранее. Архитектурные решения базовой части не должны усложняться «под будущий XMPP» — единственная допустимая подготовка: доменная логика отправки сообщений сосредоточена в `app/services/messages.py` (а не в WS-хендлере), чтобы позже её мог вызвать XMPP-шлюз.
