# DATA MODEL — PostgreSQL 16

Общие соглашения:
- PK — `BIGINT GENERATED ALWAYS AS IDENTITY` (кроме составных ключей).
- Время — `TIMESTAMPTZ NOT NULL DEFAULT now()`.
- Расширения (первая миграция): `CREATE EXTENSION IF NOT EXISTS citext; CREATE EXTENSION IF NOT EXISTS pg_trgm;`
- Enum'ы — PostgreSQL `ENUM` типы: `room_visibility ('public','private')`, `friendship_status ('pending','accepted')`, `invitation_status ('pending','accepted','declined','revoked')`.
- «Чат» = комната или DM. В таблицах, относящихся к чату, две nullable FK (`room_id`, `dm_id`) + CHECK «ровно одна заполнена» (`num_nonnulls(room_id, dm_id) = 1`). Это даёт настоящие FK с каскадами вместо полиморфной ссылки.

## 1. ER-обзор

```
users ─┬─< user_sessions
       ├─< password_reset_tokens
       ├─< friendships >─ users           (requester / addressee)
       ├─< user_bans >─ users             (banner / banned)
       ├─< rooms (owner_id)
       ├─< room_members >─ rooms
       ├─< room_bans >─ rooms
       ├─< room_invitations >─ rooms
       ├─< direct_conversations (user_a_id, user_b_id)
       ├─< messages (author_id)  >─ rooms | direct_conversations
       ├─< attachments (uploader_id) >─ messages, rooms | direct_conversations
       └─< chat_read_states >─ rooms | direct_conversations
```

## 2. Таблицы

### 2.1. `users`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| email | CITEXT | NOT NULL, UNIQUE, CHECK (length ≤ 254 и содержит `@`) |
| username | CITEXT | NOT NULL, UNIQUE, CHECK (`username ~ '^[A-Za-z0-9_.-]{3,32}$'`). **Immutable** — триггер `users_username_immutable` (BEFORE UPDATE: `IF NEW.username <> OLD.username THEN RAISE EXCEPTION`) |
| password_hash | TEXT | NOT NULL (Argon2id, формат PHC `$argon2id$...`) |
| display_name | VARCHAR(64) | NULL (опционально, изменяемо) |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now() |
| password_changed_at | TIMESTAMPTZ | NOT NULL DEFAULT now() |

### 2.2. `user_sessions`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| user_id | BIGINT | NOT NULL FK → users ON DELETE CASCADE |
| token_hash | BYTEA | NOT NULL UNIQUE (SHA-256, 32 байта) |
| persistent | BOOLEAN | NOT NULL («Keep me signed in») |
| user_agent | TEXT | NOT NULL DEFAULT '' (обрезается до 512) |
| ip | INET | NULL (из X-Forwarded-For, последний доверенный hop) |
| created_at | TIMESTAMPTZ | NOT NULL |
| last_seen_at | TIMESTAMPTZ | NOT NULL |
| expires_at | TIMESTAMPTZ | NOT NULL |

Индексы: `ix_user_sessions_user (user_id, last_seen_at DESC)`, `ix_user_sessions_expires (expires_at)` (чистка просроченных раз в час).

### 2.3. `password_reset_tokens`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| user_id | BIGINT | NOT NULL FK → users ON DELETE CASCADE |
| token_hash | BYTEA | NOT NULL UNIQUE |
| created_at | TIMESTAMPTZ | NOT NULL |
| expires_at | TIMESTAMPTZ | NOT NULL (created_at + 1 h) |
| used_at | TIMESTAMPTZ | NULL |

Индекс: `(user_id)`. При выдаче нового токена прежние неиспользованные токены пользователя удаляются.

### 2.4. `friendships`
Одна строка на неупорядоченную пару.

| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| requester_id | BIGINT | NOT NULL FK → users ON DELETE CASCADE |
| addressee_id | BIGINT | NOT NULL FK → users ON DELETE CASCADE |
| status | friendship_status | NOT NULL DEFAULT 'pending' |
| message | VARCHAR(500) | NULL (приветственный текст заявки) |
| created_at | TIMESTAMPTZ | NOT NULL |
| accepted_at | TIMESTAMPTZ | NULL |

- CHECK `requester_id <> addressee_id`.
- UNIQUE INDEX `ux_friendships_pair ON friendships (LEAST(requester_id, addressee_id), GREATEST(requester_id, addressee_id))` — запрещает встречные дубли.
- Индексы: `(addressee_id, status)`, `(requester_id, status)`.
- Отклонение заявки / удаление из друзей = `DELETE` строки.
- Встречная заявка (B шлёт A, когда A→B pending) = автоматическое принятие существующей.

### 2.5. `user_bans`
| Колонка | Тип | Ограничения |
|---|---|---|
| banner_id | BIGINT | FK → users ON DELETE CASCADE |
| banned_id | BIGINT | FK → users ON DELETE CASCADE |
| created_at | TIMESTAMPTZ | NOT NULL |

PK `(banner_id, banned_id)`, CHECK `banner_id <> banned_id`, индекс `(banned_id)`.
Проверка «есть бан между A и B» = `EXISTS (... WHERE (banner_id=A AND banned_id=B) OR (banner_id=B AND banned_id=A))`.

### 2.6. `rooms`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| name | CITEXT | NOT NULL UNIQUE, CHECK (length(name) BETWEEN 3 AND 64) |
| description | VARCHAR(1000) | NOT NULL DEFAULT '' |
| visibility | room_visibility | NOT NULL |
| owner_id | BIGINT | NOT NULL FK → users **ON DELETE CASCADE** |
| created_at | TIMESTAMPTZ | NOT NULL |
| member_count | INTEGER | NOT NULL DEFAULT 0 (денормализация для каталога; поддерживается сервисом в той же транзакции) |

Индексы:
- `ix_rooms_owner (owner_id)`
- `ix_rooms_public_name ON rooms (name) WHERE visibility = 'public'`
- `ix_rooms_search_trgm ON rooms USING gin ((name::text || ' ' || description) gin_trgm_ops) WHERE visibility = 'public'` — поиск в каталоге (`ILIKE '%q%'`).

### 2.7. `room_members`
| Колонка | Тип | Ограничения |
|---|---|---|
| room_id | BIGINT | FK → rooms ON DELETE CASCADE |
| user_id | BIGINT | FK → users ON DELETE CASCADE |
| is_admin | BOOLEAN | NOT NULL DEFAULT false |
| joined_at | TIMESTAMPTZ | NOT NULL |

PK `(room_id, user_id)`; индекс `ix_room_members_user (user_id, room_id)`; индекс `(room_id) WHERE is_admin`.
Инвариант owner: при создании комнаты owner вставляется с `is_admin=true`; сервис запрещает снятие админства/удаление/бан owner (`403 cannot_modify_owner`). Дополнительно — deferred constraint trigger `room_owner_is_admin_member`, проверяющий на commit, что для каждой изменённой комнаты есть строка `(room_id, owner_id, is_admin=true)`.

### 2.8. `room_bans`
| Колонка | Тип | Ограничения |
|---|---|---|
| room_id | BIGINT | FK → rooms ON DELETE CASCADE |
| user_id | BIGINT | FK → users ON DELETE CASCADE (забаненный) |
| banned_by_id | BIGINT | NULL FK → users ON DELETE SET NULL (в UI «Deleted user») |
| banned_at | TIMESTAMPTZ | NOT NULL |
| reason | VARCHAR(500) | NULL |

PK `(room_id, user_id)`, индекс `(user_id)`.
Бан = в одной транзакции `DELETE FROM room_members` + `INSERT INTO room_bans` + `UPDATE room_invitations SET status='revoked'` для pending-приглашений этого пользователя в эту комнату.

### 2.9. `room_invitations`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| room_id | BIGINT | FK → rooms ON DELETE CASCADE |
| invitee_id | BIGINT | FK → users ON DELETE CASCADE |
| inviter_id | BIGINT | NULL FK → users ON DELETE SET NULL |
| status | invitation_status | NOT NULL DEFAULT 'pending' |
| created_at | TIMESTAMPTZ | NOT NULL |
| responded_at | TIMESTAMPTZ | NULL |

UNIQUE INDEX `ux_room_invitations_pending ON (room_id, invitee_id) WHERE status = 'pending'`; индекс `(invitee_id, status)`.

### 2.10. `direct_conversations`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| user_a_id | BIGINT | NULL FK → users ON DELETE SET NULL |
| user_b_id | BIGINT | NULL FK → users ON DELETE SET NULL |
| user_a_orig_id | BIGINT | NOT NULL (без FK; исходный id для уникальности пары) |
| user_b_orig_id | BIGINT | NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL |

- CHECK `user_a_orig_id < user_b_orig_id` (канонический порядок), UNIQUE `(user_a_orig_id, user_b_orig_id)`.
- Индексы: `(user_a_id)`, `(user_b_id)`.
- Создаётся при принятии дружбы (у каждой пары друзей есть DM; `POST /api/dms` — get-or-create на всякий случай), повторная дружба той же пары размораживает тот же DM со всей историей. Writable-статус **не хранится**, а вычисляется: `user_a_id IS NOT NULL AND user_b_id IS NOT NULL AND friends(a,b) AND NOT banned_between(a,b)`. Удалённый собеседник → `NULL` → DM навсегда frozen для оставшегося.
- Если обе стороны удалены (оба `NULL`) — DM удаляется вместе с сообщениями/файлами (сервис удаления аккаунта проверяет это).

### 2.11. `messages`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK, задаёт хронологию |
| room_id | BIGINT | NULL FK → rooms ON DELETE CASCADE |
| dm_id | BIGINT | NULL FK → direct_conversations ON DELETE CASCADE |
| author_id | BIGINT | NULL FK → users ON DELETE SET NULL («Deleted user») |
| client_msg_id | UUID | NULL (идемпотентность) |
| content | TEXT | NOT NULL DEFAULT '', CHECK `octet_length(content) <= 3072` |
| reply_to_message_id | BIGINT | NULL FK → messages ON DELETE SET NULL |
| created_at | TIMESTAMPTZ | NOT NULL |
| edited_at | TIMESTAMPTZ | NULL (≠ NULL → пометка `edited`) |
| deleted_at | TIMESTAMPTZ | NULL (мягкое удаление; content очищается в '') |
| deleted_by_id | BIGINT | NULL FK → users ON DELETE SET NULL |

CHECK `num_nonnulls(room_id, dm_id) = 1`.
CHECK «reply в том же чате» обеспечивается сервисом (кросс-строчная проверка).

Индексы (ключевые для 10 000+ сообщений):
- `ix_messages_room_id ON messages (room_id, id DESC) WHERE room_id IS NOT NULL`
- `ix_messages_dm_id ON messages (dm_id, id DESC) WHERE dm_id IS NOT NULL`
- `ux_messages_client ON messages (author_id, client_msg_id) WHERE client_msg_id IS NOT NULL` (UNIQUE)
- `ix_messages_reply ON messages (reply_to_message_id) WHERE reply_to_message_id IS NOT NULL`
- `ix_messages_content_trgm ON messages USING gin (content gin_trgm_ops) WHERE deleted_at IS NULL` — поиск по истории.

Курсорная пагинация (index-only range scan, стоимость не зависит от глубины истории). В сервисе страница берётся **в два шага**: сначала `SELECT id … ORDER BY id DESC LIMIT n+1` (Index Only Scan по `ix_messages_room_id`), затем строки с JOIN'ами `WHERE id IN (…)`. Запрос целых строк одним SELECT планировщик может выполнить как «обратный скан PK + фильтр room_id», который деградирует для комнат, чья история лежит далеко в прошлом (см. тест `test_history_page_is_fast_and_uses_index`):

```sql
-- старее курсора (infinite scroll вверх)
SELECT ... FROM messages
WHERE room_id = :room_id AND id < :before_id
ORDER BY id DESC LIMIT :limit + 1;          -- +1 → has_more
-- новее курсора (догрузка после reconnect)
SELECT ... FROM messages
WHERE room_id = :room_id AND id > :after_id
ORDER BY id ASC LIMIT :limit + 1;
```

Цитата ответа подтягивается LEFT JOIN `messages r ON r.id = m.reply_to_message_id` (id, author, первые 200 символов content, deleted_at).

### 2.12. `attachments`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| message_id | BIGINT | NULL FK → messages ON DELETE CASCADE (NULL = pending) |
| room_id | BIGINT | NULL FK → rooms ON DELETE CASCADE |
| dm_id | BIGINT | NULL FK → direct_conversations ON DELETE CASCADE |
| uploader_id | BIGINT | NULL FK → users ON DELETE SET NULL |
| stored_path | TEXT | NOT NULL UNIQUE (относительно `/data/uploads`) |
| original_filename | VARCHAR(255) | NOT NULL (санитизирован: без `/ \ \0`, управляющих символов) |
| mime_type | VARCHAR(127) | NOT NULL |
| size_bytes | BIGINT | NOT NULL, CHECK `size_bytes > 0 AND size_bytes <= 20971520 AND (NOT is_image OR size_bytes <= 3145728)` |
| is_image | BOOLEAN | NOT NULL |
| width / height | INTEGER | NULL (для изображений, для резервирования места в ленте) |
| comment | VARCHAR(1000) | NULL |
| created_at | TIMESTAMPTZ | NOT NULL |

CHECK `num_nonnulls(room_id, dm_id) = 1`.
Индексы: `(message_id)`, `(room_id)`, `(dm_id)`, `ix_attachments_pending (created_at) WHERE message_id IS NULL`.
Мягкое удаление сообщения удаляет его строки `attachments` (и файлы после commit).

### 2.13. `chat_read_states`
| Колонка | Тип | Ограничения |
|---|---|---|
| id | BIGINT identity | PK |
| user_id | BIGINT | NOT NULL FK → users ON DELETE CASCADE |
| room_id | BIGINT | NULL FK → rooms ON DELETE CASCADE |
| dm_id | BIGINT | NULL FK → direct_conversations ON DELETE CASCADE |
| last_read_message_id | BIGINT | NOT NULL DEFAULT 0 (без FK — сообщение может быть удалено) |
| updated_at | TIMESTAMPTZ | NOT NULL |

CHECK `num_nonnulls(room_id, dm_id) = 1`; UNIQUE `(user_id, room_id) WHERE room_id IS NOT NULL`; UNIQUE `(user_id, dm_id) WHERE dm_id IS NOT NULL`.
- `mark_read` — `UPDATE ... SET last_read_message_id = GREATEST(last_read_message_id, :id)` (монотонно).
- При вступлении в комнату создаётся с `last_read_message_id = max(id)` комнаты (старая история не считается непрочитанной). До этапа 4 (таблицы `messages` ещё нет) — `0`.
- При выходе/бане строка удаляется.

Подсчёт непрочитанного (ограничен 100 → UI «99+»), использует `ix_messages_room_id`:

```sql
SELECT crs.room_id, (
  SELECT count(*) FROM (
    SELECT 1 FROM messages m
    WHERE m.room_id = crs.room_id AND m.id > crs.last_read_message_id
      AND m.deleted_at IS NULL AND m.author_id IS DISTINCT FROM :user_id
    LIMIT 100) t) AS unread
FROM chat_read_states crs WHERE crs.user_id = :user_id AND crs.room_id IS NOT NULL;
```

## 3. Матрица каскадных удалений

### 3.1. `Delete room` (только owner; `DELETE FROM rooms WHERE id = :id`)

| Объект | Поведение | Механизм |
|---|---|---|
| `room_members` | удаляются | FK CASCADE |
| `room_bans` | удаляются | FK CASCADE |
| `room_invitations` | удаляются | FK CASCADE |
| `messages` комнаты | удаляются безвозвратно | FK CASCADE |
| `attachments` комнаты | удаляются (строки) | FK CASCADE (через room_id и message_id) |
| Файлы на диске | `rmtree /data/uploads/rooms/{id}` | сервис, после commit; GC как страховка |
| `chat_read_states` | удаляются | FK CASCADE |
| `messages.reply_to_message_id` извне | невозможно (reply только в том же чате) | — |
| WS | `room_access_revoked{reason:"room_deleted"}` всем бывшим участникам | сервис, после commit |

### 3.2. `Delete account` (сервис `users.delete_account`, одна транзакция)

Порядок:
1. `SELECT id FROM rooms WHERE owner_id = :uid` → запомнить для удаления каталогов и WS-событий.
2. `DELETE FROM users WHERE id = :uid`. Далее всё по FK:

| Объект | Поведение | Механизм |
|---|---|---|
| `user_sessions`, `password_reset_tokens` | удаляются | CASCADE |
| `rooms` где owner | удаляются + всё из §3.1 | CASCADE через `rooms.owner_id` |
| `room_members` в чужих комнатах | удаляются (членство) | CASCADE; сервис уменьшает `member_count` (до DELETE) |
| `room_bans` где user — забаненный | удаляются | CASCADE |
| `room_bans.banned_by_id` = user | остаются, `banned_by_id = NULL` | SET NULL |
| `room_invitations` (invitee) | удаляются | CASCADE |
| `room_invitations.inviter_id` | SET NULL | SET NULL |
| `friendships`, `user_bans` (обе стороны) | удаляются | CASCADE |
| `messages` в чужих комнатах | **остаются**, `author_id = NULL` | SET NULL |
| `attachments` в чужих комнатах | **остаются** (строки и файлы), `uploader_id = NULL` | SET NULL |
| `direct_conversations` | остаются у собеседника, сторона → `NULL`, DM frozen | SET NULL; если обе стороны NULL — сервис удаляет DM + `rmtree dms/{id}` |
| `messages` в DM | остаются, `author_id = NULL` | SET NULL |
| `chat_read_states` пользователя | удаляются | CASCADE |

3. После commit: `rmtree` каталогов удалённых комнат (и полностью осиротевших DM); `room_access_revoked{reason:"room_deleted"}` участникам удалённых комнат; `dm_frozen{reason:"user_deleted"}` собеседникам; `presence_update offline`; закрыть все WS пользователя.

### 3.3. Прочие удаления

| Операция | Что удаляется | Что остаётся |
|---|---|---|
| Выход из комнаты | `room_members`, `chat_read_states` | сообщения и файлы пользователя в комнате |
| Бан в комнате | `room_members`, `chat_read_states`, pending-приглашения → `revoked` | сообщения и файлы; запись `room_bans` |
| Удаление сообщения | content → '', `deleted_at`, строки `attachments` + файлы | строка `messages` (плейсхолдер, цитаты ответов показывают «deleted») |
| Удаление из друзей | строка `friendships` | DM и история (frozen до новой дружбы) |
| User-ban | `friendships` пары (любой статус) | DM-история (frozen), запись `user_bans` |
| Revoke сессии / Sign out | строка `user_sessions` | всё остальное |

## 4. Миграции
- `0001_extensions` (citext, pg_trgm; ENUM-типы создаются в миграциях своих таблиц), `0002_users_sessions`, `0003_rooms` (+ `chat_read_states` только с `room_id` и CHECK `num_nonnulls(room_id) = 1`), `0004_check_constraint_names` (исправляет удвоенные имена CHECK из 0002/0003: `op.create_table` применял naming convention к уже полным именам; в новых миграциях полные имена оборачиваются в `op.f()`), `0005_messages` (только `room_id`), `0006_contacts_dms` (добавляет `dm_id` в `messages` и `chat_read_states`: FK, partial UNIQUE/индексы и расширяет CHECK до `num_nonnulls(room_id, dm_id) = 1`), `0007_attachments` — нарезка по этапам roadmap; триггеры (`users_username_immutable`, `room_owner_is_admin_member`) — в миграциях как raw SQL.
- Seed-данных нет. Тестовая БД `chat_test` создаётся `db/init/01-create-test-db.sql` (с расширениями), схема накатывается фикстурой pytest через `alembic upgrade head`, каждый тест — в транзакции с откатом / `TRUNCATE ... RESTART IDENTITY CASCADE`.
