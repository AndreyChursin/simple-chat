# API & WebSocket Specification

Базовый путь REST: `/api`. Формат: JSON (UTF-8), кроме загрузки файлов (multipart) и скачивания (binary). Аутентификация: cookie `chat_session` (см. ARCHITECTURE.md §6). Все изменяющие запросы (POST/PUT/PATCH/DELETE) требуют заголовок `Origin` из `APP_ORIGIN`: другой **или отсутствующий** → `403 origin_mismatch` (браузер шлёт его всегда; curl/скрипты должны ставить сами). WS-handshake — так же, иначе close 4003. Каждый HTTP-ответ несёт `X-Request-ID` (он же в JSON-логах).

## 0. Общие правила

### 0.1. Формат ошибки
```json
{ "error": { "code": "room_name_taken", "message": "Room name is already taken", "details": {} } }
```

### 0.2. Коды
| HTTP | Когда | Примеры `code` |
|---|---|---|
| 400 | Невалидный ввод (включая ошибки Pydantic) | `validation_error`, `empty_message`, `empty_file`, `invalid_reply_target`, `invalid_attachment`, `message_deleted`, `username_immutable`, `owner_cannot_leave`, `cannot_ban_self` |
| 401 | Нет/просрочена/отозвана сессия; неверный логин | `not_authenticated`, `invalid_credentials`, `invalid_current_password` |
| 403 | Аутентифицирован, но нет прав / запрещено правилом | `forbidden`, `not_room_admin`, `not_room_owner`, `not_message_author`, `cannot_modify_owner`, `room_banned`, `user_banned`, `dm_frozen`, `not_friends`, `origin_mismatch` |
| 404 | Не существует **или** скрыто (приватная комната для не-участника, чужое вложение) | `not_found`, `room_not_found`, `user_not_found`, `message_not_found`, `attachment_not_found` |
| 409 | Конфликт состояния/уникальности | `email_taken`, `username_taken`, `room_name_taken`, `already_member`, `already_friends`, `friend_request_exists`, `invitation_exists`, `invitation_not_pending`, `request_not_pending`, `attachment_already_sent`, `already_banned`, `user_is_banned_in_room`, `already_admin`, `not_admin` |
| 410 | Токен сброса пароля использован/просрочен | `reset_token_invalid` |
| 413 | Превышен лимит | `message_too_large` (>3072 байт), `image_too_large` (>3 МБ), `file_too_large` (>20 МБ), `too_many_attachments` (>10) |
| 429 | Rate limit | `rate_limited` (+ заголовок `Retry-After`) |

### 0.3. Общие объекты

```ts
UserPublic   = { id: number, username: string, display_name: string|null, presence: "online"|"AFK"|"offline" }
             // удалённый пользователь: { id: null, username: "Deleted user", display_name: null, presence: "offline" }
ChatRef      = { type: "room"|"dm", id: number }
Attachment   = { id, original_filename, mime_type, size_bytes, is_image, width, height, comment,
                 download_url: "/api/attachments/{id}/download" }
Message      = { id, chat: ChatRef, author: UserPublic, content: string, created_at, edited_at: string|null,
                 deleted: boolean, reply_to: null | { id, author: UserPublic, excerpt: string, deleted: boolean },
                 attachments: Attachment[], client_msg_id: string|null }
RoomSummary  = { id, name, description, visibility, member_count, owner: UserPublic,
                 my_role: "owner"|"admin"|"member"|null, unread: number, created_at }
             // unread заполняется в GET /rooms/mine и GET /rooms/{id}; в остальных ответах и WS-событиях — 0
             // (живые счётчики — GET /api/me/unread и unread_update)
Page<T>      = { items: T[], has_more: boolean, next_cursor: number|null }
```

Даты — ISO 8601 UTC. Все списки пользователей отдают актуальный `presence` из in-memory менеджера.

---

## 1. Auth

| Метод и путь | Тело | Ответ | Ошибки |
|---|---|---|---|
| `POST /api/auth/register` | `{email, username, password}` | `201 {user}` + Set-Cookie (сразу залогинен, persistent=false) | `400 validation_error` (email формат; username `^[A-Za-z0-9_.-]{3,32}$`; пароль 8–128 символов), `409 email_taken`, `409 username_taken` |
| `POST /api/auth/login` | `{email, password, keep_signed_in: bool}` | `200 {user}` + Set-Cookie | `401 invalid_credentials` (одинаково для неизвестного email и неверного пароля), `429` |
| `POST /api/auth/logout` | — | `204`, cookie очищен. Удаляет **только текущую** сессию, закрывает WS только этой сессии | `401` |
| `GET /api/auth/me` | — | `200 {user: {id, email, username, display_name, created_at}}` | `401` |
| `POST /api/auth/password/forgot` | `{email}` | `202` всегда; если email существует — письмо со ссылкой `{APP_ORIGIN}/reset-password?token=...` | `429` |
| `POST /api/auth/password/reset` | `{token, new_password}` | `204`; все сессии пользователя удалены (WS закрыты `4001`) | `400`, `410 reset_token_invalid` |
| `POST /api/auth/password/change` | `{current_password, new_password}` | `204`; остальные сессии пользователя завершаются, текущая остаётся | `401 invalid_current_password`, `400` |

## 2. Sessions

| Метод и путь | Ответ | Ошибки |
|---|---|---|
| `GET /api/sessions` | `200 {items: [{id, browser: "Chrome 131", os: "Windows 11", device: "Desktop", user_agent, ip, created_at, last_seen_at, persistent, is_current}]}` (сортировка: текущая первая, затем `last_seen_at DESC`) | `401` |
| `DELETE /api/sessions/{id}` | `204`; сессия удалена, её WS получают `session_revoked` и close `4001`. Удаление текущей = logout (cookie очищается) | `404` (чужая/несуществующая) |
| `DELETE /api/sessions?scope=others` | `204`; все, кроме текущей | — |

## 3. Profile / Account

| Метод и путь | Тело | Ответ | Ошибки |
|---|---|---|---|
| `GET /api/users/{username}` | — | `200 UserPublic + {friendship: "none"|"pending_out"|"pending_in"|"friends", banned_by_me: bool, has_banned_me: bool}` (поля дружбы/банов добавляются на этапе 6; до него — только `UserPublic`) | `404 user_not_found` |
| `GET /api/users?search=q&limit=20` | — | `200 {items: UserPublic[]}` (префиксный поиск по username, ≥ 2 символов) | `400` |
| `PATCH /api/me` | `{display_name}` (строка ≤ 64 после trim; `""`/`null` → сброс) | `200 {user}` | `400 username_immutable` если в теле есть `username`; `400 validation_error` для любого другого поля (в т.ч. email — смена email вне scope) |
| `DELETE /api/me` | `{password}` | `204`; каскад по DATA_MODEL.md §3.2, cookie очищен; всем вкладкам всех сессий `session_revoked` + close 4001, presence → `offline` сразу (без grace). События другим: см. DATA_MODEL.md §3.2 п. 3 | `401 invalid_current_password`, `400 validation_error` (нет `password`) |

## 4. Contacts, Friends, User-Bans

| Метод и путь | Тело | Ответ | Ошибки |
|---|---|---|---|
| `GET /api/contacts` | — | `200 {friends: [UserPublic + {dm_id, unread}], incoming: [{id, from: UserPublic, message, created_at}], outgoing: [{id, to: UserPublic, message, created_at}], past_dms: [{dm_id, peer: UserPublic, frozen: true, unread}]}` | — |
| `POST /api/friend-requests` | `{username, message?}` (message ≤ 500) | `201 {request: {id, to: UserPublic, message, created_at}}`, адресату `friend_request_received`; если есть встречная pending-заявка — она принимается: `200 {friendship: "accepted", friend: UserPublic, dm_id}` | `404 user_not_found`, `400 validation_error` (сам себе, message > 500), `409 already_friends`, `409 friend_request_exists`, `403 user_banned` (бан в любую сторону) |
| `POST /api/friend-requests/{id}/accept` | — | `200 {friend: UserPublic, dm_id}` — DM создаётся (или размораживается старый) здесь же; обоим `friend_added`, заявителю `friend_request_resolved{accepted}`, при старом DM — `dm_unfrozen` | `404 not_found` (не адресат), `409 request_not_pending` |
| `POST /api/friend-requests/{id}/decline` | — | `204` (строка удаляется); заявителю `friend_request_resolved{declined}` | `404` (не адресат / не pending) |
| `DELETE /api/friend-requests/{id}` | — | `204` (отзыв своей исходящей); адресату `friend_request_resolved{cancelled}` | `404` |
| `DELETE /api/friends/{user_id}` | — | `204`; обоим `friend_removed` и `dm_frozen{reason:"not_friends"}` | `404` |
| `GET /api/user-bans` | — | `200 {items: [{user: UserPublic, created_at}]}` (кого забанил я) | — |
| `POST /api/user-bans` | `{user_id}` | `201 {user: UserPublic, created_at}`; удаляет дружбу/заявку пары (дружба → обоим `friend_removed`; заявка → обоим `friend_request_resolved{cancelled}`); при наличии DM обоим `dm_frozen{reason:"user_banned"}` | `404 user_not_found`, `400` (сам себя), `409 already_banned` |
| `DELETE /api/user-bans/{user_id}` | — | `204`; дружба **не** восстанавливается (DM остаётся frozen до новой дружбы; обоим `dm_frozen` с новой причиной, обычно `not_friends`) | `404` |

Ограничения забаненного (B забанен A): B не может отправить A заявку (`403 user_banned`), писать в DM (`403 dm_frozen`), пригласить A в комнату (`403 user_banned`). A тоже не может писать B в DM (frozen для обоих). Видимость сообщений друг друга в общих комнатах **не** меняется.

## 5. Rooms

| Метод и путь | Тело/параметры | Ответ | Ошибки |
|---|---|---|---|
| `GET /api/rooms/catalog?search=&cursor=&limit=30` | `search` по name/description (ILIKE, trgm) | `200 Page<RoomSummary + {is_banned}>` — **только public**, сортировка `member_count DESC, id`; `is_banned` — для текущего пользователя. `cursor` = `next_cursor` = id последней комнаты страницы (keyset по её текущему `member_count`; если она удалена — пустая страница) | `400` |
| `GET /api/rooms/mine` | — | `200 {public: RoomSummary[], private: RoomSummary[]}` (с `unread`) | — |
| `POST /api/rooms` | `{name, description?, visibility}` | `201 RoomSummary` (создатель = owner + admin + member) | `400`, `409 room_name_taken` |
| `GET /api/rooms/{id}` | — | `200 RoomSummary + {admins: UserPublic[]}` (admins включают owner, он первый). Для не-участника public-комнаты — `my_role: null` (превью + кнопка Join) | `404 room_not_found` (не существует или private и я не участник); `403 room_banned` для public при бане |
| `PATCH /api/rooms/{id}` | `{name?, description?, visibility?}` | `200 RoomSummary`; всем участникам `room_updated` | `403 not_room_owner` (в т.ч. admin и не-участник public), `404` (private, не участник), `409 room_name_taken`, `400` |
| `DELETE /api/rooms/{id}` | — | `204`; каскад DATA_MODEL.md §3.1 | `403 not_room_owner`, `404` |
| `POST /api/rooms/{id}/join` | — | `200 RoomSummary` (только public; для private — только через приглашение) | `404` (private/нет), `403 room_banned`, `409 already_member` |
| `POST /api/rooms/{id}/leave` | — | `204`; мне `room_access_revoked{reason:"left"}` (во все вкладки) | `400 owner_cannot_leave`, `404 room_not_found` (не участник) |
| `GET /api/rooms/{id}/members?cursor=&limit=200&search=` | — | `200 Page<UserPublic + {role, joined_at}>` — owner, затем admins, затем members; внутри: online → AFK → offline, затем username. Сортировка — на сервере в памяти (presence не в БД); `cursor` = `next_cursor` = user_id последнего элемента; `search` — подстрока username; `limit` ≤ 1000 | `403 forbidden` (public, не участник), `403 room_banned`, `404` (private, не участник) |

## 6. Invitations

| Метод и путь | Тело | Ответ | Ошибки |
|---|---|---|---|
| `POST /api/rooms/{id}/invitations` | `{username}` | `201 {invitation: {id, room: {id, name, description, visibility, member_count}, invitee: UserPublic, inviter: UserPublic, created_at}}`; приглашённому `invitation_received` | `403 forbidden` (не участник public-комнаты; для private — `404`), `404 user_not_found`, `409 already_member`, `409 invitation_exists`, `409 user_is_banned_in_room` (сначала разбанить), `403 user_banned` (личный бан между пригласившим и приглашаемым; проверка появляется на этапе 6 вместе с `user_bans`) |
| `GET /api/invitations` | — | `200 {items: [{id, room: {id, name, description, visibility, member_count}, inviter: UserPublic, created_at}]}` — мои pending | — |
| `POST /api/invitations/{id}/accept` | — | `200 RoomSummary` (если уже участник — приглашение просто помечается `accepted`) | `404` (нет или не моё), `409 invitation_not_pending`, `403 room_banned` (если забанили после приглашения) |
| `POST /api/invitations/{id}/decline` | — | `204` | `404` (нет, не моё или уже не pending) |
| `GET /api/rooms/{id}/invitations` | — | `200 {items: [{id, invitee, inviter, created_at}]}` — pending | `403 not_room_admin` |
| `DELETE /api/rooms/{id}/invitations/{inv_id}` | — | `204` (статус `revoked`) | `403 not_room_admin`, `404` |

Правило: приглашать может любой участник комнаты (кнопка `Invite user` в правой панели); просмотр и отзыв pending-приглашений — admin/owner. Приглашать можно и в public-комнату. Вступление в комнату любым путём (join/accept) помечает pending-приглашения этого пользователя в эту комнату как `accepted`.

## 7. Moderation (roles, room bans)

Все операции берут блокировку строки комнаты (как join/leave), коммитят и только потом рассылают события. `RoomMember = UserPublic + {role, joined_at}`, `RoomBan = {user: UserPublic, banned_by: UserPublic, banned_at, reason: string|null}` (`banned_by` удалённого пользователя — «Deleted user»).

| Метод и путь | Ответ | Права | Ошибки |
|---|---|---|---|
| `POST /api/rooms/{id}/admins/{user_id}` | `200 RoomMember`; всем участникам `member_role_changed{role:"admin"}` | owner | `403 not_room_owner` (admin, member, не-участник public), `404 not_found` (цель не участник), `409 already_admin` (в т.ч. owner) |
| `DELETE /api/rooms/{id}/admins/{user_id}` | `200 RoomMember`; всем участникам `member_role_changed{role:"member"}` | owner или admin (admin может снять и себя) | `403 cannot_modify_owner` (цель — owner), `403 not_room_admin`, `404 not_found` (цель не участник), `409 not_admin` |
| `DELETE /api/rooms/{id}/members/{user_id}` | `204` — **удаление = бан** без причины (эквивалент POST bans) | owner или admin | как у POST bans; `404 not_found`, если цель не участник |
| `POST /api/rooms/{id}/bans` `{user_id, reason?≤500}` | `201 RoomBan`; если цель была участником — ей `room_access_revoked{reason:"banned"}` (во все вкладки, подписка на комнату снимается сразу), оставшимся участникам `member_removed{reason:"banned"}` | owner или admin | `400 cannot_ban_self`, `403 cannot_modify_owner`, `403 not_room_admin`, `404 user_not_found`, `409 already_banned`, `400 validation_error` |
| `GET /api/rooms/{id}/bans` | `200 {items: RoomBan[]}`, новые первыми | owner или admin | `403 not_room_admin` |
| `DELETE /api/rooms/{id}/bans/{user_id}` | `204`, событий нет (пользователь **не** возвращается автоматически — может войти в public/быть приглашён в private) | owner или admin | `403 not_room_admin`, `404 not_found` (не забанен) |

Бан доступен и для не-участника (превентивный бан по `user_id`; событий нет). Бан в одной транзакции удаляет членство и `chat_read_states`, переводит pending-приглашения в эту комнату в `revoked`; сообщения и файлы пользователя остаются. Админ может банить другого админа (кроме owner). Для private-комнаты не-участник получает `404 room_not_found` на любой эндпоинт модерации.

## 8. Direct Messages

| Метод и путь | Ответ | Ошибки |
|---|---|---|
| `POST /api/dms` `{user_id}` | `200 {dm_id, peer: UserPublic, frozen: false, frozen_reason: null}` — get-or-create (обычно DM уже создан при принятии дружбы) | `403 not_friends`, `403 user_banned`, `404 user_not_found`, `400` (сам себе) |
| `GET /api/dms/{dm_id}` | `200 {dm_id, peer: UserPublic, frozen: bool, frozen_reason: "not_friends"|"user_banned"|"user_deleted"|null}` | `404` (не сторона) |

В замороженном DM запрещены отправка, редактирование и удаление (`403 dm_frozen`, `details.reason`); чтение истории, поиск и `around` доступны обеим сторонам. Живые события (`new_message`, `typing`) идут только по незамороженным DM — подписка на чат DM есть, пока стороны друзья.

## 9. Messages (одинаково для комнат и DM)

`{chat}` = `rooms/{room_id}` или `dms/{dm_id}`. Доступ: участник комнаты / сторона DM; иначе `404` для private/DM, `403` для public-комнаты, где не участник.

| Метод и путь | Тело/параметры | Ответ | Ошибки |
|---|---|---|---|
| `GET /api/{chat}/messages` | `before_id?`, `after_id?` (взаимоисключающие), `limit` 1–100 (default 50) | `200 Page<Message>`; `before_id`/без курсора — `items` по возрастанию id, последние `limit` перед курсором, `next_cursor` = минимальный id; `after_id` — следующие после курсора | `400` (оба курсора), `403`, `404` |
| `GET /api/{chat}/messages/around/{message_id}?limit=50` | — | `200 {items, has_more_before, has_more_after}` (переход к цитате) | `404` |
| `GET /api/{chat}/messages/search?q=&before_id=&limit=30` | `q` ≥ 2 символов после trim (подстрока, без учёта регистра; удалённые не ищутся) | `200 Page<Message>` — **от новых к старым**, `next_cursor` = минимальный id страницы (передаётся как `before_id`) | `400`, `403`, `404` |
| `POST /api/{chat}/messages` | `{content, reply_to_message_id?, attachment_ids?: number[], client_msg_id?: uuid}` | `201 Message`; fan-out `new_message` | `400 empty_message` (пусто и нет вложений), `413 message_too_large`, `413 too_many_attachments`, `400 invalid_reply_target` (другой чат/не существует), `400` (вложение не моё/не pending/другой чат), `403 dm_frozen`, `403`, `404`, `429` |
| `PATCH /api/messages/{id}` | `{content}` | `200 Message` (`edited_at` установлен; тот же текст — без изменений и без события); `message_edited` | `403 not_message_author`, `403 dm_frozen`, `400 message_deleted`, `400 empty_message` (пустое без вложений), `413 message_too_large`, `404 message_not_found`; потерян доступ к чату — `403`/`404` как для чата |
| `DELETE /api/messages/{id}` | — | `204`; `message_deleted`. Повторное удаление — `204` без события | `403 forbidden` (не автор и не admin комнаты; в DM — только автор), `403 dm_frozen`, `404 message_not_found` |
| `GET /api/me/unread` | — | `200 {items: [{chat: ChatRef, unread: number, last_read_message_id: number, last_message_id: number\|null}]}` — все мои комнаты и все мои DM (в т.ч. frozen). `unread` ≤ 100; считаются неудалённые чужие сообщения после курсора | — |
| `POST /api/{chat}/read` | `{last_read_message_id ≥ 0}` | `204`; курсор только растёт (`GREATEST`) и не уходит дальше последнего сообщения чата; всем вкладкам пользователя `unread_update`. Frozen DM тоже можно отметить | `400 validation_error`, `403 forbidden` (public-комната, не участник), `403 room_banned`, `404` (private, нет чата, не сторона DM) |

`attachment_ids` — свои pending-вложения этого же чата (иначе `400 invalid_attachment`), не больше 10 (`413 too_many_attachments`); с вложениями текст может быть пустым, и правка может оставить сообщение без текста. Удаление сообщения удаляет его вложения (строки и файлы). `around` возвращает окно `limit` (2–100) с центром на сообщении: `limit/2` до него и остальное начиная с него. Отправка ограничена 5/с на пользователя (WS и REST вместе). Повтор `client_msg_id` того же автора возвращает уже созданное сообщение (`201`, без повторной рассылки) и не расходует лимит.

Длина `content` проверяется в байтах UTF-8 после нормализации переносов строк `\r\n → \n`; ведущие/хвостовые пробелы сохраняются, но строка из одних пробелов считается пустой.

## 10. Attachments

| Метод и путь | Тело | Ответ | Ошибки |
|---|---|---|---|
| `POST /api/{chat}/attachments` | multipart: `file` (обязательно), `comment?` (≤ 1000) | `201 Attachment` (pending) | `413 file_too_large` (>20 971 520 байт), `413 image_too_large` (сигнатура изображения и >3 145 728 байт), `400 empty_file`, `400 validation_error` (не multipart, нет части `file`, нет имени, длинный comment), `403 dm_frozen`, `403`/`404` по доступу |
| `PATCH /api/attachments/{id}` | `{comment}` (≤ 1000, `""`/`null` — сброс) | `200 Attachment` — только пока pending | `404` (нет или чужое pending-вложение — его существование не раскрывается), `403` (чужое отправленное — на практике раньше срабатывает `409`), `409 attachment_already_sent` |
| `DELETE /api/attachments/{id}` | — | `204` (только pending, только загрузивший; файл удаляется с диска) | `409 attachment_already_sent` (удалять через сообщение), `403`, `404` |
| `GET /api/attachments/{id}/download?inline=0|1` | — | `200` поток файла (заголовки — ARCHITECTURE.md §5.3); поддержка `Range` | `404` (нет / нет доступа *сейчас* / потерян доступ к комнате), `401` |

Имя файла при вставке из буфера (`Ctrl+V`) без имени: клиент задаёт `pasted-image-YYYYMMDD-HHMMSS.png`.

## 11. Health
`GET /api/health` → `200 {"status":"ok","db":"ok"}` | `503 {"status":"error","db":"error"}` (формат healthcheck'а, не единый формат ошибок).

---

## 12. WebSocket-протокол

**Endpoint:** `GET /ws?tab_id=<uuid>`. Сообщения — JSON-текстовые фреймы. Каждое сообщение имеет `type`; запросы клиента могут иметь `req_id` (строка), который сервер возвращает в `ack`/`error`.

### 12.1. Коды закрытия
| Код | Значение | Действие клиента |
|---|---|---|
| 4001 | Не аутентифицирован / сессия отозвана / аккаунт удалён | Перейти на экран логина, не переподключаться |
| 4003 | Origin не разрешён | Не переподключаться |
| 4008 | Переполнена очередь отправки (медленный клиент) | Переподключиться + догрузка `after_id` |
| 1009 | Фрейм > 16 КБ | Баг клиента |
| 1003 | Бинарный фрейм | Баг клиента |
| 1001/1006/1011 | Рестарт/обрыв | Reconnect с экспоненциальной задержкой 0.5 с → 10 с, ±20% jitter |

### 12.2. Client → Server

```jsonc
// поддержка соединения (каждые 25 с), не считается активностью
{ "type": "heartbeat" }

// пользователь взаимодействовал со вкладкой (throttle 10 с)
{ "type": "activity" }

// печатает (throttle 3 с на чат)
{ "type": "typing", "chat": { "type": "room", "id": 12 } }

// отправка сообщения (текст и/или ранее загруженные вложения)
{ "type": "send_message", "req_id": "r-17",
  "chat": { "type": "dm", "id": 5 },
  "client_msg_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
  "content": "Hello\nworld 👋",
  "reply_to_message_id": 1042,          // optional
  "attachment_ids": [77] }              // optional

// пометить прочитанным до id включительно; ответ — `unread_update` во все вкладки
// (ошибки как у REST: error{code: forbidden | room_banned | room_not_found | not_found})
{ "type": "mark_read", "chat": { "type": "room", "id": 12 }, "last_read_message_id": 1050 }
```

Редактирование и удаление сообщений — через REST (§9); результаты приходят всем по WS. Неизвестный `type` или невалидное событие → `error{code:"validation_error"}`; > 20 событий/с на соединение → `error{code:"rate_limited", req_id:null}` (событие отброшено). `typing` — только в чат, где пользователь участник (иначе `error forbidden`), не возвращается самому печатающему.

### 12.3. Server → Client

```jsonc
// при подключении — всегда первое событие; приходит, когда чаты соединения уже подписаны
// (события, случившиеся за время подключения, идут следом, не теряются)
{ "type": "hello", "user_id": 3, "session_id": 41, "server_time": "2026-11-23T10:00:00Z",
  "heartbeat_interval_ms": 25000, "afk_timeout_ms": 60000 }
{ "type": "presence_snapshot", "users": [ { "user_id": 7, "presence": "AFK" }, ... ] }  // сразу после hello: не-offline из аудитории; не перечисленные — offline

// подтверждение и ошибки
{ "type": "ack", "req_id": "r-17", "message": { /* Message */ } }
{ "type": "error", "req_id": "r-17", "code": "message_too_large", "message": "Message exceeds 3072 bytes" }
//  коды: message_too_large | empty_message | dm_frozen | forbidden | not_found | room_not_found | invalid_reply_target
//        | internal_error
//        | too_many_attachments | invalid_attachment | room_banned | rate_limited | validation_error

// сообщения (всем online-участникам чата, во все вкладки, включая отправителя)
{ "type": "new_message", "message": { /* Message */ } }
{ "type": "message_edited", "message": { /* Message */ } }
{ "type": "message_deleted", "chat": { "type": "room", "id": 12 }, "message_id": 1042,
  "deleted_by": { "id": 9, "username": "mod" } }
{ "type": "typing", "chat": { "type": "room", "id": 12 }, "user": { "id": 7, "username": "bob" } }

// presence (аудитории пользователя: друзья + участники общих комнат, а также его собственным вкладкам), < 2 с
{ "type": "presence_update", "user_id": 7, "presence": "online" | "AFK" | "offline" }

// непрочитанное (синхронизация между вкладками и сессиями; после mark_read и при вступлении в чат)
{ "type": "unread_update", "chat": { "type": "dm", "id": 5 }, "unread": 0, "last_read_message_id": 1050 }

// комнаты
{ "type": "room_access_revoked", "room_id": 12, "reason": "banned" | "left" | "room_deleted" }
{ "type": "room_joined", "room": { /* RoomSummary */ } }            // мне, во все вкладки (join/accept invite/create)
{ "type": "room_updated", "room": { /* RoomSummary, my_role — роль получателя */ } }
{ "type": "member_joined", "room_id": 12, "user": { /* UserPublic */ }, "member_count": 31 }
{ "type": "member_removed", "room_id": 12, "user_id": 7, "reason": "left" | "banned" | "account_deleted", "member_count": 30 }
// account_deleted: клиент переподписывает закэшированные сообщения этого пользователя как "Deleted user"
{ "type": "member_role_changed", "room_id": 12, "user_id": 7, "role": "admin" | "member" }
{ "type": "invitation_received", "invitation": { "id": 3, "room": { "id": 12, "name": "dev" }, "inviter": { /* UserPublic */ } } }

// контакты и DM
{ "type": "friend_request_received", "request": { "id": 4, "from": { /* UserPublic */ }, "message": "Hi!" } }
{ "type": "friend_request_resolved", "request_id": 4, "result": "accepted" | "declined" | "cancelled" }
{ "type": "friend_added", "user": { /* UserPublic */ }, "dm_id": 5 }
{ "type": "friend_removed", "user_id": 7 }
{ "type": "dm_frozen", "dm_id": 5, "reason": "user_banned" | "not_friends" | "user_deleted" }
{ "type": "dm_unfrozen", "dm_id": 5 }                                 // снова друзья и нет бана

// сессии
{ "type": "session_revoked" }   // сразу за ним close 4001
```

### 12.4. Гарантии доставки и порядок
- Внутри одного соединения события доставляются в порядке публикации (одна очередь на соединение).
- `new_message` для одного чата публикуются в порядке commit; клиент дополнительно сортирует по `id` и дедуплицирует по `id` / `client_msg_id`.
- Отправитель получает и `new_message` (во все свои вкладки), и `ack` (в отправившую); порядок этих двух событий не гарантирован.
- Неподтверждённые `send_message` клиент повторяет после каждого reconnect с тем же `client_msg_id`.
- WS — канал уведомлений, источник истины — REST. После reconnect клиент: `GET /api/me/unread`, для открытого чата `GET messages?after_id=<last_id>`, перезапрос `GET /api/contacts` и `GET /api/rooms/mine`.
- `send_message` при закрытом сокете: клиент ставит сообщение в локальную очередь со статусом «sending» и отправляет после reconnect с тем же `client_msg_id` (сервер вернёт существующее при повторе).
