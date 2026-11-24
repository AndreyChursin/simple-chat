# ARCHITECTURE — Online Chat Server

## 1. Нефункциональные требования (из ТЗ, разделы 1, 3, 7)

| Требование | Значение | Как обеспечиваем |
|---|---|---|
| Одновременные пользователи | 300 (×N вкладок/сессий) | Один async-процесс uvicorn, in-memory WS-менеджер, ~1–2 тыс. соединений без проблем |
| Участников в комнате | до 1000 | Fan-out по in-memory индексу `chat → online users`, без запросов к БД на каждое сообщение |
| Комнат / контактов на пользователя | без лимита (ориентир 20 / 50) | Индексы по `user_id` во всех таблицах членства |
| Доставка сообщения | < 3 с | Commit → немедленный fan-out в том же event loop (типично < 100 мс) |
| Обновление presence | < 2 с | Presence-тикер 1 с + немедленный пересчёт при connect/disconnect |
| История | 10 000+ сообщений | Курсорная пагинация по `(chat, id)`, виртуализированный список на фронте |
| Хранение | годы | PostgreSQL в volume `pgdata`, файлы в volume `uploads_data` |
| Лимиты | текст 3 КБ, изображение 3 МБ, файл 20 МБ | Валидация в сервисах + потоковый подсчёт байт + nginx `client_max_body_size 21m` |
| Запуск | `docker compose up` | Всё в контейнерах, авто-миграции, без seed-данных |

## 2. Контейнеры (`compose.yaml`)

```
                 браузер (http://localhost:8080)
                          │
              ┌───────────▼────────────┐
              │ frontend (nginx:1.27)  │  статика SPA (собрана в multi-stage build node:20 → nginx)
              │  /        → index.html │
              │  /api/*   → backend    │  proxy_pass, client_max_body_size 21m, X-Forwarded-For
              │  /ws      → backend    │  Upgrade/Connection headers, proxy_read_timeout 3600s
              └───────────┬────────────┘
                          │ http://backend:8000
              ┌───────────▼────────────┐        ┌────────────────────┐
              │ backend (python:3.12)  │──SMTP──▶ mailpit             │ UI :8025, SMTP :1025
              │ uvicorn --workers 1    │        └────────────────────┘
              │ /data/uploads (volume) │
              └───────────┬────────────┘
                          │ postgresql+asyncpg://db:5432/chat
              ┌───────────▼────────────┐
              │ db (postgres:16)       │ volume pgdata
              └────────────────────────┘
```

### 2.1. Сервисы

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-chat}
      POSTGRES_USER: ${POSTGRES_USER:-chat}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-chat}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro      # создаёт chat_test для pytest
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 3s
      timeout: 3s
      retries: 20
    restart: unless-stopped

  mailpit:
    image: axllent/mailpit:v1.21
    ports: ["${MAILPIT_UI_PORT:-8025}:8025"]                            # SMTP 1025 только внутри сети
    restart: unless-stopped

  backend:
    build: ./backend
    env_file: { path: .env, required: false }
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-chat}:${POSTGRES_PASSWORD:-chat}@db:5432/${POSTGRES_DB:-chat}
      TEST_DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-chat}:${POSTGRES_PASSWORD:-chat}@db:5432/chat_test
      UPLOAD_DIR: /data/uploads
      APP_ORIGIN: ${APP_ORIGIN:-http://localhost:8080}
      SMTP_HOST: ${SMTP_HOST:-mailpit}
      SMTP_PORT: ${SMTP_PORT:-1025}
    volumes:
      - uploads_data:/data/uploads
    depends_on:
      db: { condition: service_healthy }
      mailpit: { condition: service_started }
    # CMD задан в backend/Dockerfile:
    # ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*' --ws-ping-interval 20 --ws-ping-timeout 20 --ws-max-size 16384 --ws-per-message-deflate false"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=2)"]
      interval: 5s
      timeout: 3s
      retries: 20
      start_period: 10s
    restart: unless-stopped

  frontend:
    build: ./frontend
    ports: ["${APP_PORT:-8080}:80"]
    depends_on:
      backend: { condition: service_healthy }
    restart: unless-stopped

volumes:
  pgdata:
  uploads_data:
```

- **Авто-миграции:** `alembic upgrade head` в `CMD` образа backend перед uvicorn (переопределение `command`, напр. `run --rm backend pytest`, миграции не запускает — тесты накатывают их сами). Если миграция падает — контейнер не стартует (healthcheck не пройдёт, frontend не поднимется).
- **Без seed-данных:** ни миграции, ни lifespan не создают пользователей/комнат.
- **`.env` необязателен:** все переменные имеют дефолты; `.env.example` документирует их (в т.ч. `SMTP_HOST/PORT/USER/PASSWORD/USE_TLS`, `MAIL_FROM`, `APP_ORIGIN`, `APP_PORT`, `SESSION_TTL_DAYS=30`).
- `/api/health` — `200 {"status":"ok","db":"ok"}` (делает `SELECT 1`), иначе `503`.

### 2.2. nginx (frontend/nginx.conf, ключевое)

```nginx
client_max_body_size 21m;
location /api/ { proxy_pass http://backend:8000; proxy_set_header Host $host;
                 proxy_set_header X-Forwarded-For $remote_addr;
                 proxy_request_buffering off; }   # потоковая проверка размера на backend
location /ws   { proxy_pass http://backend:8000; proxy_http_version 1.1;
                 proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";
                 proxy_set_header X-Forwarded-For $remote_addr;
                 proxy_read_timeout 3600s; }
location /     { try_files $uri /index.html; }
# НЕТ location для /data/uploads — файлы отдаёт только backend.
```

Один origin для SPA, API и WS → cookie-аутентификация без CORS.

## 3. Слои backend

```
api/ (FastAPI routers: парсинг, Pydantic-схемы, коды ответов)
  └─▶ services/ (бизнес-правила, транзакции, вызовы access.*)
        ├─▶ db/models (SQLAlchemy 2.x async)
        ├─▶ services/files.py (диск)
        └─▶ ws/manager.py (публикация событий ПОСЛЕ commit)
ws/handlers.py (входящие WS-события) ──▶ те же services/
```

- **Правило «события после commit»:** каждая изменяющая функция сервиса сама делает `commit()` и только после него вызывает `manager.publish(...)` / `send_to_users(...)` (отдельного `UnitOfWork` нет). Исключение или откат до commit → событий нет.
- `services/access.py` — единственное место проверок: `require_room_member`, `require_room_admin`, `require_room_owner`, `require_dm_writable`, `require_attachment_readable`, `is_user_banned_between(a, b)`.

## 4. WebSocket-менеджер и presence

### 4.1. Подключение
- URL: `GET /ws` (через nginx). Аутентификация — cookie `chat_session` при handshake; невалидная/просроченная сессия → close `4001`. Проверка `Origin` → иначе close `4003`.
- Каждая вкладка = одно WS-соединение. Клиент генерирует `tab_id` (UUID в `sessionStorage`) и передаёт в query: `/ws?tab_id=...`.

### 4.2. Структуры в памяти (`app/ws/manager.py`)

```python
@dataclass
class Connection:
    conn_id: str            # uuid4
    user_id: int
    session_id: int         # user_sessions.id
    tab_id: str
    ws: WebSocket
    last_activity: float    # time.monotonic() последнего activity
    connected_at: float
    send_queue: asyncio.Queue[str]   # maxsize=1000; переполнение → закрыть соединение (4008 slow consumer)

class ConnectionManager:
    conns: dict[str, Connection]                    # conn_id → Connection
    by_user: dict[int, set[str]]                    # user_id → conn_ids
    by_session: dict[int, set[str]]                 # session_id → conn_ids (для selective logout / revoke)
    chat_subscribers: dict[ChatKey, set[int]]       # ("room", id) | ("dm", id) → online user_ids
    user_chats: dict[int, set[ChatKey]]             # user_id → чаты пользователя (загружается при первом connect)
    presence: dict[int, Presence]                   # последнее разосланное состояние (нет ключа = offline)
    _grace: dict[int, asyncio.TimerHandle]          # пользователи без соединений в течение grace-периода
    clock: Callable[[], float]                      # time.monotonic; подменяется в тестах
```

- У каждого соединения — отдельная writer-корутина, читающая `send_queue` и пишущая в сокет. Fan-out = `put_nowait` в очереди (O(получателей), без await на медленных клиентах). Комната на 1000 участников при 300 online → ≤ 300×вкладки put'ов, микросекунды.
- `user_chats`/`chat_subscribers` загружаются из БД при первом соединении пользователя и удаляются при последнем отключении. Изменения членства (join/leave/ban/invite accept/room delete/DM create/friend remove не влияет на DM-подписку) обновляют индексы через `manager.on_membership_changed(...)`.

### 4.3. Алгоритм presence

Клиент (каждая вкладка):
- слушает `mousemove`, `mousedown`, `keydown`, `wheel`, `touchstart`, `focus`, `visibilitychange→visible`;
- при активности шлёт `{"type":"activity"}` не чаще 1 раза в **10 с** (throttle, leading edge — первое действие после простоя уходит сразу);
- раз в **25 с** шлёт `{"type":"heartbeat"}` (поддержка соединения, не считается активностью).

Сервер:
- `activity` → `conn.last_activity = now`; если агрегированный статус пользователя был `AFK` → немедленный пересчёт и рассылка (переход AFK→online мгновенный).
- connect → немедленный пересчёт (offline→online).
- disconnect не последнего соединения → немедленный пересчёт (закрытая вкладка могла быть единственной активной → AFK). Disconnect последнего → таймер **grace 1 с** (`loop.call_later`, не тикер): если за это время пришло новое соединение (перезагрузка страницы), таймер отменяется и никто ничего не видит; иначе ровно через 1 с — `offline`. `user_chats`/подписки пользователя живут до конца grace (аудитория offline-рассылки считается по ним).
- **Presence-тикер** (`asyncio` task, период 1 с): для каждого пользователя с соединениями:

```python
def aggregate(user_id) -> Presence:
    conns = by_user.get(user_id)
    if not conns:                                  return "offline"
    if any(now - c.last_activity < 60 for c in conns): return "online"
    return "AFK"
```

  Тикер нужен только для перехода online→AFK; остальные переходы событийные. Если результат отличается от `presence[user_id]` → `presence_update` аудитории (включая вкладки самого пользователя). Худшая задержка перехода online→AFK: 60 с + ≤ 1 с тикера (требование «> 1 минуты» соблюдено, рассылка < 2 с после факта).
- Новое соединение считается активным (`last_activity = connected_at`).
- **Без permessage-deflate:** события короткие, а сжатие идёт отдельно на каждое соединение (zlib-контекст и CPU на каждый кадр каждому получателю) — при fan-out в большую комнату это было главной статьёй CPU. Пул БД фиксированный (`pool_size=30`, без overflow): новое соединение — это SCRAM-рукопожатие на Python в event loop, и «одноразовые» overflow-соединения под всплеском подключений тормозили доставку на секунды (найдено нагрузочным тестом).
- **Мёртвые соединения** (обрыв сети без close): uvicorn ping 20 с / timeout 20 с → disconnect. Нормальное закрытие вкладки/браузера даёт close frame сразу → offline ≤ 2 с.
- Скрытые вкладки браузер может троттлить — это не влияет на корректность: соединение открыто (≠ offline), активности нет (→ AFK через 60 с), что соответствует ТЗ.

**Аудитория presence** = друзья (`friendships.status='accepted'`) ∪ участники общих комнат. Отдельного SQL и кэша нет: аудитория считается в памяти как объединение `chat_subscribers` по чатам пользователя (`user_chats`) — это ровно те, у кого есть соединения, а рассылать offline-пользователям нечего; индексы уже обновляются при каждом изменении членства. Друзья входят в аудиторию через подписку на чат их DM: при загрузке `user_chats` берутся только DM, стороны которых сейчас друзья (`friendships.status='accepted'`); принятие дружбы подписывает обоих, удаление друга и user-ban — отписывают. Замороженный DM подписки не даёт, поэтому бывший друг presence не видит (UI в замороженном DM статус собеседника не показывает). После `hello` соединение получает `presence_snapshot` — не-offline пользователей из аудитории (сам пользователь включительно); всех остальных клиент считает по полю `presence` из REST (`GET /api/rooms/{id}/members` и т. п.), которое берётся из того же `manager.presence`.

### 4.4. Доставка сообщений (< 3 с)

```
client A ──send_message(WS) или POST /messages──▶ services.messages.create()
   ├─ access.require_room_member / require_dm_writable
   ├─ валидация 3072 байт, reply_to в том же чате, attachment_ids принадлежат A и pending
   ├─ INSERT messages, UPDATE attachments SET message_id, COMMIT
   └─▶ manager.publish(("room", id), new_message)  → все online-участники (все их вкладки)
       + message_ack отправителю (с client_msg_id)
```

- Офлайн-доставка: сообщения лежат в БД; при входе клиент получает непрочитанные через `GET /api/me/unread` и историю через REST.
- **Восстановление после переподключения:** клиент хранит `last_seen_message_id` по каждому открытому чату и после reconnect вызывает `GET .../messages?after_id=` (до 200, затем перезагрузка истории). Это закрывает окно потерь между обрывом и reconnect.
- Идемпотентность: `client_msg_id` (UUID) уникален в паре `(author_id, client_msg_id)` → повтор после reconnect не создаёт дубль, возвращает существующее сообщение.

### 4.5. Отзыв доступа и сессий
- Бан/выход/удаление комнаты → удалить `("room", id)` из `user_chats[user]`, user из `chat_subscribers`, отправить `room_access_revoked` всем вкладкам пользователя. Все следующие REST-запросы проверяются по БД → `403/404`.
- Revoke сессии / sign out → для `by_session[session_id]`: отправить `session_revoked`, закрыть с кодом `4001`. Другие сессии не трогаются.
- Удаление аккаунта → закрыть все соединения пользователя (`4001`), presence → offline для аудитории.

### 4.6. Ограничения и защита
- Входящие WS-фреймы ≤ 16 КБ (больше → close `1009`). Rate limit: 20 событий/с на соединение, `send_message` ≤ 5/с на пользователя → `error{code:"rate_limited"}`.
- `typing` не персистится, fan-out с throttle 3 с на (user, chat).

### 4.7. Масштабирование (вне scope, зафиксировано для будущего)
Один процесс держит требуемые 300 пользователей с большим запасом. Переход на несколько воркеров потребует вынести `publish` в Redis pub/sub (или PostgreSQL `LISTEN/NOTIFY`) и presence в Redis — интерфейс `manager.publish(chat_key, event)` спроектирован так, чтобы это была замена реализации.

## 5. Файловое хранилище

### 5.1. Раскладка на диске (volume `uploads_data` → `/data/uploads`)

```
/data/uploads/
  rooms/{room_id}/{yyyy}/{mm}/{uuid4}      # без расширения, без пользовательских имён
  dms/{dm_id}/{yyyy}/{mm}/{uuid4}
  tmp/{uuid4}.part                          # во время загрузки
```

`attachments.stored_path` хранит относительный путь (`rooms/12/2026/11/3f2a...`). Абсолютный путь строится как `UPLOAD_DIR / stored_path` с проверкой `resolve().is_relative_to(UPLOAD_DIR)`.

### 5.2. Загрузка (`POST /api/rooms/{id}/attachments` | `POST /api/dms/{id}/attachments`, multipart)
1. `access.require_chat_writable` (участник комнаты / DM не заморожен).
2. Тело разбирается потоково (`python_multipart.MultipartParser` поверх `request.stream()`, `app/services/uploads.py`): часть `file` пишется в `tmp/{uuid}.part` по мере прихода, счётчик проверяется после каждого чанка; > 20 МБ → прервать, удалить `.part`, `413 file_too_large`. `Content-Length` не учитывается, кроме досрочного отказа, если он сам по себе больше лимита. Если первые байты — сигнатура изображения, лимит сразу 3 МБ (`413 image_too_large` без дочитывания). Тела > `client_max_body_size` отклоняет nginx — тоже JSON в едином формате ошибки.
3. Тип — по содержимому: сигнатура PNG/JPEG/GIF/WEBP **и** `Pillow.verify()` с совпадающим форматом → `is_image=true`, `mime_type` и `width/height` из файла. Иначе — обычный файл; `mime_type` — заявленный клиентом, если он синтаксически валиден, иначе `application/octet-stream` (отдаётся всё равно как `application/octet-stream`). Имя файла санитизируется (без путей и управляющих символов, ≤ 255; `%22`/`%0D`/`%0A` из multipart-кодировки браузеров декодируются) и на диск не попадает.
4. `os.replace` в целевой путь, INSERT `attachments` с `message_id = NULL` (pending), вернуть метаданные.
5. Вложение привязывается к сообщению при `send_message` (`attachment_ids`, ≤ 10 на сообщение). Pending-вложения старше 24 ч удаляет GC.

### 5.3. Отдача (`GET /api/attachments/{id}/download`)
- Проверка **на каждый запрос**: вложение привязано к чату, где пользователь *сейчас* участник (комната: строка в `room_members`; DM: одна из сторон). Pending-вложение доступно только загрузившему. Нет доступа → `404` (не раскрываем существование).
- `FileResponse` с заголовками: `Content-Type` = сохранённый `mime_type` (для не-изображений — `application/octet-stream`), `Content-Disposition: attachment; filename*=UTF-8''<original_filename>` (для изображений с `?inline=1` — `inline`), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`, `Cache-Control: private, max-age=0, must-revalidate`.
- Превью изображений в ленте — тот же эндпоинт с `?inline=1` (cookie отправляется браузером автоматически, same-origin).

### 5.4. Удаление и сборка мусора
| Событие | Действие с файлами |
|---|---|
| Удаление сообщения | Строки `attachments` сообщения удаляются в транзакции; после commit — `unlink` файлов |
| Удаление комнаты | Транзакция `DELETE FROM rooms` (каскад в БД); после commit — `shutil.rmtree(/data/uploads/rooms/{id})` в `to_thread` |
| Удаление аккаунта | Для каждой комнаты, где user — owner: как «удаление комнаты». Файлы пользователя в чужих комнатах **не трогаются** |
| Выход/бан из комнаты | Файлы **не трогаются** |
| GC (раз в час + при старте) | 1) удалить pending-вложения старше 24 ч (строка + файл); 2) обойти дерево и удалить файлы без строки в БД старше 1 ч (защита от падения между commit и unlink); 3) удалить `tmp/*.part` старше 1 ч; 4) удалить каталоги комнат и DM, которых нет в БД, — только если в них ничего не менялось 1 ч (новый чат, созданный после снимка id, не трогается); 5) удалить пустые каталоги `yyyy/mm` старше 1 ч (перемещение файла повторяет `mkdir`, если GC успел удалить каталог) |

Ошибки `unlink` логируются и не откатывают удаление в БД — повторно подберёт GC.

## 6. Аутентификация

- Токен сессии: `secrets.token_urlsafe(32)` → cookie `chat_session`; в БД `token_hash = sha256(token)`.
- Cookie: `HttpOnly; SameSite=Lax; Path=/; Secure` — если `APP_ORIGIN` https. «Keep me signed in» → `Max-Age=2592000` (30 дн.); `Max-Age` cookie перевыпускается при каждом `GET /api/auth/me` (SPA вызывает его при загрузке). Без галочки — cookie без `Max-Age`, серверный TTL 24 ч.
- На каждый запрос: поиск сессии по хешу, проверка `expires_at`; не чаще 1 раза в минуту — обновление `last_seen_at`, `ip` и скользящее продление `expires_at = now + TTL` (30 дн. / 24 ч).
- IP: nginx **перезаписывает** `X-Forwarded-For` значением `$remote_addr` (клиент не может подделать IP на экране Sessions), uvicorn `--proxy-headers` кладёт его в `request.client`.
- Хеширование Argon2id выполняется в worker-потоке (`anyio.to_thread`), чтобы не блокировать event loop; для неизвестного email выполняется фиктивная проверка (одинаковое время ответа).
- CSRF: SameSite=Lax + проверка `Origin` для POST/PUT/PATCH/DELETE и WS: заголовок обязателен и должен входить в `APP_ORIGIN` (отсутствующий тоже отклоняется).
- Rate limit (in-memory): login 10/мин на IP+email, forgot-password 3/час на email и 20/час на IP → `429`.

## 7. Наблюдаемость
- Структурированные JSON-логи (`app/core/logging.py`): одна JSON-строка на запись (`ts`, `level`, `logger`, `msg`, `request_id`, `user_id`, поля `extra`); на каждый HTTP-запрос строка `request` с `method`, `path`, `status`, `latency_ms` (healthcheck — на уровне DEBUG); `X-Request-ID` в ответе. Access-лог uvicorn отключён.
- `GET /api/health`; метрики в логах раз в минуту (`app.metrics`): `connections`, `users_online`, `users_afk`, `frames_sent`, `fanout_p95_ms` — p95 задержки от постановки кадра в очередь соединения до записи в сокет.
