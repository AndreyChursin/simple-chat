# ROADMAP & DEFINITION OF DONE

Разработка — вертикальными слайсами: каждый этап даёт работающую функциональность от миграции до UI. После каждого этапа:

1. `docker compose down -v && docker compose up --build` — стек поднимается с нуля, миграции применяются, данных нет.
2. `docker compose exec backend pytest -q` — зелёный; фронтенд — `typecheck` + `vitest` зелёные.
3. Чек-лист этапа отмечен.
4. **`git commit`** (Conventional Commits) → **`/clear`** в Claude Code, следующий этап начинается с чтения CLAUDE.md и нужных разделов `docs/`.

---

## Этап 1 — Каркас и инфраструктура
**Scope:** `compose.yaml` (db, mailpit, backend, frontend), volumes `pgdata`/`uploads_data`, healthchecks, backend (FastAPI, config, единый формат ошибок, `/api/health`, Alembic + миграция `0001` с `citext`/`pg_trgm`), `db/init/01-create-test-db.sql`, pytest-фикстуры (БД `chat_test`, `AsyncClient`), frontend (Vite+TS strict, nginx с proxy `/api` и `/ws`, `client_max_body_size 21m`), `.env.example`, README с запуском.
- [x] `docker compose up --build` на чистой машине с одним Docker → http://localhost:8080 открывает SPA, `/api/health` = 200.
- [x] Миграции применяются автоматически; повторный старт — идемпотентен.
- [x] Нет seed-данных.
- [x] `docker compose exec backend pytest` работает против `chat_test`.

**Commit:** `chore: project skeleton with docker compose, migrations, test infra` → `/clear`

## Этап 2 — Аккаунты, сессии, пароли
**Scope:** `users`, `user_sessions`, `password_reset_tokens`; register/login/logout/me; Keep me signed in; Sessions (список, revoke, revoke others); change password; forgot/reset через Mailpit; Profile (display_name, username read-only); экраны Sign in/up, Forgot/Reset, Sessions, Profile (без Delete account); верхнее меню.
- [x] Email и username уникальны (регистронезависимо); username неизменяем (API `400`, триггер БД).
- [x] Argon2id; `401 invalid_credentials` одинаков для неверного email/пароля.
- [x] Keep me signed in: сессия переживает перезапуск браузера; без галочки — session cookie.
- [x] Sign out завершает только текущую сессию (тест: 2 сессии, logout одной — другая жива).
- [x] Sessions показывают браузер/ОС/IP/время; revoke чужой сессии работает.
- [x] Письмо сброса видно в Mailpit; токен одноразовый, TTL 1 ч, после сброса все сессии завершены.
- [x] Rate limit login/forgot → `429`.

**Commit:** `feat(auth): registration, sessions, password reset` → `/clear`

## Этап 3 — Комнаты: создание, каталог, вход/выход, приватные и приглашения
**Scope:** `rooms`, `room_members`, `room_invitations`, `chat_read_states` (создание при join); CRUD комнаты, каталог с поиском, join/leave, приглашения (создание/accept/decline/revoke), удаление комнаты (пока без файлов); Main Chat Layout (nav sidebar с аккордеоном expanded/compact, пустая центральная зона, context panel с участниками), Create room, Public Rooms Catalog, Private Rooms + Invitations.
- [ ] Уникальное имя комнаты (`409`), public видны в каталоге с описанием и числом участников, поиск работает.
- [ ] Private комнаты не видны в каталоге и `404` для не-участников; вход только по приглашению.
- [ ] Owner не может выйти (`400 owner_cannot_leave`), может удалить комнату.
- [ ] Layout: без открытого чата — nav expanded, ctx скрыт; в комнате — nav compact accordion, справа участники.

**Commit:** `feat(rooms): rooms, catalog, membership, invitations` → `/clear`

## Этап 4 — Сообщения и WebSocket-ядро
**Scope:** `messages`; WS endpoint, `ConnectionManager` (очереди, `chat_subscribers`, `user_chats`), `hello`, `send_message`/`ack`/`error`, `new_message`, `message_edited`, `message_deleted`, `typing`; REST история с курсорами (`before_id`, `after_id`, `around`), поиск, edit/delete (автор), reply; Chat Window (virtuoso, infinite scroll вверх, умный автоскролл, «N new messages»), Composer (multiline, Enter/Shift+Enter, emoji, reply, edit, счётчик байт), reconnect с догрузкой `after_id`, идемпотентность `client_msg_id`.
- [ ] Текст: UTF-8, multiline, emoji; > 3072 байт → `413`/WS `message_too_large`; пустое → `400`.
- [ ] Ответ с цитатой; reply в другой чат → `400 invalid_reply_target`.
- [ ] Редактирование только автором, пометка `edited`; удаление автором.
- [ ] Доставка < 3 с во все вкладки всех участников (WS-тест с таймингом).
- [ ] История 10 000 сообщений: запрос страницы < 50 мс (тест с генерацией 10k строк, `EXPLAIN` использует `ix_messages_room_id`), прокрутка плавная.
- [ ] Автоскролл не дёргает ленту при чтении истории; при нахождении внизу — следует за новыми.
- [ ] Офлайн-пользователь видит пропущенные сообщения при следующем входе.

**Commit:** `feat(messages): realtime messaging, history pagination, composer` → `/clear`

## Этап 5 — Presence и multi-tab
**Scope:** `activity`/`heartbeat`, presence-тикер 1 с, grace 1 с на disconnect, агрегация по вкладкам и сессиям, аудитория (друзья + общие комнаты), `presence_snapshot`, `presence_update`; закрытие WS при logout/revoke (`session_revoked`, 4001); индикаторы ●/◐/○ в nav, context panel, Manage Room.
- [ ] `online`: активность хотя бы в одной вкладке за 60 с.
- [ ] `AFK`: открытые вкладки есть, но ни в одной нет активности > 60 с (тест с подменой часов).
- [ ] `offline`: все вкладки во всех сессиях закрыты.
- [ ] Рассылка изменения < 2 с (тест: closing last socket → `presence_update offline` ≤ 2 с).
- [ ] Перезагрузка страницы не даёт мигания offline.
- [ ] Revoke сессии мгновенно разлогинивает вкладки этой сессии и только их.

**Commit:** `feat(presence): multi-tab online/AFK/offline aggregation` → `/clear`

## Этап 6 — Контакты, user-ban, личные сообщения
**Scope:** `friendships`, `user_bans`, `direct_conversations`; заявки (по username и из списка участников комнаты, с приветствием), accept/decline/cancel, удаление друга, user-ban/unban, DM (get-or-create, сообщения/edit/delete/reply/история/поиск — переиспользуя этап 4), вычисляемый frozen, `dm_frozen`/`dm_unfrozen`, friend-события; экран Contacts, DM в nav, карточка собеседника, frozen composer.
- [ ] Дружба только после подтверждения; удалить можно в любой момент.
- [ ] DM возможна только между друзьями без бана (`403 not_friends` / `403 dm_frozen`).
- [ ] User-ban: дружба разорвана, новые DM/заявки/приглашения в комнаты заблокированы; история видна обоим read-only (нельзя писать, редактировать, удалять).
- [ ] DM функционально совпадает с комнатой: текст, reply, edit, delete своих, вложения (после этапа 7), история.

**Commit:** `feat(contacts): friends, user bans, direct messages` → `/clear`

## Этап 7 — Вложения и файлы
**Scope:** `attachments`; потоковая загрузка с лимитами, детект изображений, pending → привязка к сообщению, comment, защищённое скачивание (`Content-Disposition`, `nosniff`, `CSP sandbox`, Range), удаление файлов при удалении сообщения/комнаты, GC (pending > 24 ч, сироты, `.part`); Composer: кнопка, **Ctrl+V**, drag-and-drop, чипы с прогрессом и комментарием, клиентская предпроверка; превью изображений и лайтбокс.
- [ ] Изображение > 3 МБ → `413 image_too_large`; файл > 20 МБ → `413 file_too_large` (тест без доверия `Content-Length`).
- [ ] Сохраняется `original_filename`, при скачивании отдаётся оригинальное имя (включая кириллицу/emoji).
- [ ] Вставка из буфера обмена загружает изображение.
- [ ] Скачивание только участником комнаты/стороной DM **в момент запроса**; после выхода/бана — `404`; прямого URL на диск нет (nginx не раздаёт uploads).
- [ ] Удаление комнаты удаляет каталог файлов с диска (тест проверяет ФС).
- [ ] Файлы пользователя остаются в комнате после его выхода/бана.

**Commit:** `feat(attachments): uploads, paste, protected downloads, file GC` → `/clear`

## Этап 8 — Роли, модерация, Manage Room
**Scope:** `room_bans`; назначение/снятие админов, бан (= удаление участника), разбан, бан-лист с `banned_by`/`banned_at`, удаление сообщений админом, settings (owner), отзыв доступа (`room_access_revoked`, `member_removed`, `member_role_changed`); модальное окно `Manage Room` с 5 вкладками; popover участника с действиями.
- [ ] Owner — всегда admin, не может быть разжалован/забанен (`403 cannot_modify_owner`).
- [ ] Admin: удаляет любые сообщения, банит/разбанивает, видит бан-лист (кто/когда), снимает админство с других админов; не может назначать админов и удалять комнату.
- [ ] Удаление участника = бан; забаненный не может вернуться в public-комнату и не может быть приглашён до разбана.
- [ ] После бана пользователь сразу теряет доступ к сообщениям/файлам через UI и API.
- [ ] Все 5 вкладок `[Members] [Admins] [Banned users] [Invitations] [Settings]` работают с правильной видимостью по ролям.

**Commit:** `feat(moderation): roles, room bans, manage room modal` → `/clear`

## Этап 9 — Непрочитанное, удаление аккаунта, нагрузка, полировка
**Scope:** `GET /api/me/unread`, `mark_read`, `unread_update`, бейджи в nav/меню/`document.title`; Delete account (каскад DATA_MODEL.md §3.2 + файлы + WS); нагрузочный скрипт `tests/load` (300 WS-клиентов, 20 комнат, комната на 1000 участников, замер p95 доставки и presence); прогон полного DoD; README (запуск, переменные SMTP, архитектура кратко).
- [ ] Бейджи непрочитанного у комнат и контактов, сброс при открытии чата, синхронизация между вкладками.
- [ ] Delete account удаляет только owned-комнаты (+ сообщения/файлы с диска), членства в чужих; сообщения в чужих комнатах остаются.
- [ ] Нагрузочный тест: 300 одновременных пользователей, p95 доставки < 3 с, p95 presence < 2 с, ошибок нет.
- [ ] Весь чек-лист DoD ниже отмечен.

**Commit:** `feat: unread indicators, account deletion, load test; base product complete` → `git tag v1.0.0` → `/clear`

## Этап 10 (опционально) — Section 6: Jabber/XMPP и федерация
**Начинать только после того, как этапы 1–9 завершены и DoD базового продукта полностью зелёный.**
**Scope:** XMPP c2s-шлюз (подключение стандартными Jabber-клиентами, напр. Gajim/Psi: логин, ростер = друзья, 1-to-1 сообщения, MUC = комнаты, presence), s2s-федерация; `compose.yaml` с двумя экземплярами (Server A ↔ Server B, отдельные БД/volumes/домены); нагрузочный тест 50+ клиентов на A и 50+ на B с межсерверным обменом; admin-дашборд (активные Jabber-подключения, статистика федеративного трафика: сообщений/с, ошибки, задержки). Детальная спецификация пишется отдельным документом `docs/XMPP_FEDERATION.md` в начале этапа.
- [ ] Стандартный Jabber-клиент подключается, переписывается с пользователем веб-UI.
- [ ] Сообщения ходят между Server A и Server B.
- [ ] Нагрузочный тест 50+50 проходит.
- [ ] Дашборд показывает подключения и статистику федерации.
- [ ] Базовый DoD не регрессировал.

**Commit:** `feat(xmpp): jabber gateway and federation` → `/clear`

---

## Definition of Done — базовый продукт (разделы 1–5, 7, Appendix A)

### Инфраструктура и сдача (1, 3, 7)
- [ ] Публичный Git-репозиторий; `docker compose up` (и `up --build`) из корня поднимает всё; на хосте нужен только Docker.
- [ ] PostgreSQL в отдельном контейнере, данные в volume `pgdata`; файлы — в volume `uploads_data` на локальной ФС (не S3, не BLOB).
- [ ] Миграции применяются автоматически при старте; seed/демо-данных нет.
- [ ] Dev SMTP (Mailpit) работает; SMTP настраивается через `.env`.
- [ ] 300 одновременных пользователей; комната до 1000 участников; неограниченное число комнат/контактов на пользователя.
- [ ] Доставка сообщения < 3 с; обновление presence < 2 с.
- [ ] История 10 000+ сообщений работает плавно (курсорная пагинация + виртуализация).

### Аккаунты и сессии (2.1, 2.2, 3.5)
- [ ] Регистрация: уникальный email, уникальный неизменяемый username, пароль (Argon2id); без верификации email.
- [ ] Вход по email+password; Keep me signed in; нет автологаута по неактивности.
- [ ] Экран Sessions: все активные сессии с браузером и IP; завершение любой выбранной.
- [ ] Sign out завершает только текущую сессию браузера.
- [ ] Сброс пароля по email; смена пароля авторизованным пользователем.
- [ ] Delete account: аккаунт удалён; удалены только owned-комнаты вместе с сообщениями/файлами/изображениями (с диска и из БД); членства в чужих комнатах удалены; чужие комнаты и их содержимое остались.

### Presence (2.2)
- [ ] `online` / `AFK` (> 1 мин без активности во всех вкладках) / `offline` (все вкладки закрыты) с агрегацией по вкладкам и сессиям на сервере.
- [ ] Статусы видны в контактах, участниках комнаты, Manage Room.

### Контакты и личные сообщения (2.3, 2.5.1)
- [ ] Заявка в друзья по username и из списка участников комнаты, с опциональным текстом; требует подтверждения; удаление из друзей.
- [ ] User-ban: никакой связи от забаненного; дружба разорвана; новые DM заблокированы; история видна обоим в read-only.
- [ ] DM только между друзьями без взаимного бана.
- [ ] DM = комната из 2 участников без админов: текст, reply, edit, delete своих, вложения, история/поиск.

### Комнаты и модерация (2.4)
- [ ] Комната: уникальное name, description, public/private, owner, admins, members, banned users (кто и когда).
- [ ] Каталог public-комнат: название, описание, число участников, поиск; вход для любого незабаненного.
- [ ] Private — скрыты, вход только по приглашению.
- [ ] Любой участник может выйти; owner — не может, только удалить комнату.
- [ ] Удаление комнаты безвозвратно удаляет сообщения, файлы и изображения (включая диск).
- [ ] Owner: права admin + управление админами, удаление любого участника, удаление комнаты; всегда admin.
- [ ] Admin: удаление любых сообщений, бан/исключение, просмотр бан-листа (кто/когда), разбан, снятие админства (кроме owner).
- [ ] Исключение = бан; вернуться нельзя до разбана (даже в public).
- [ ] При потере доступа — немедленный запрет на сообщения/файлы через UI и API; загруженные файлы остаются в комнате.

### Сообщения, вложения, уведомления (2.5, 2.6, 2.7)
- [ ] Plain text, multiline, emoji, вложения, reply с цитатой; UTF-8; ≤ 3 КБ (`413` при превышении).
- [ ] Редактирование своих с пометкой `edited`; удаление автором или admin/owner (в комнате).
- [ ] Хронологический порядок; infinite scroll вверх; офлайн-сообщения доставляются при следующем входе.
- [ ] Изображения ≤ 3 МБ, файлы ≤ 20 МБ (`413`); загрузка кнопкой и вставкой из буфера; сохраняется оригинальное имя; опциональный комментарий.
- [ ] Скачивание только через защищённый эндпоинт с проверкой текущего членства.
- [ ] Бейджи непрочитанного у комнат и контактов, сброс при открытии чата.

### Интерфейс (4, Appendix A)
- [ ] Верхнее меню: `Logo | Public Rooms | Private Rooms | Contacts | Sessions | Profile ▼ | Sign out`.
- [ ] Сворачиваемая nav-панель: поиск, аккордеон Public/Private Rooms с бейджами, Contacts со статусами и бейджами, `Create room`; compact accordion после входа в комнату.
- [ ] Центр: заголовок, лента (автоскролл только если внизу, infinite scroll вверх), композер (emoji, attach + paste, `Replying to: …`, multiline, `Send`).
- [ ] Правая панель: room info, Owner, Admins, Members со статусами, `Invite user`, `Manage room`.
- [ ] `Manage Room`: `[Members] [Admins] [Banned users] [Invitations] [Settings]`.

### Качество
- [ ] Каждый эндпоинт покрыт тестами успеха и всех ошибок из API_AND_WS_SPEC.md (в т.ч. `403` по правам, `413` по лимитам).
- [ ] WS-тесты: доставка, presence-переходы, отзыв доступа, selective logout.
- [ ] Тест каскадов: Delete room и Delete account проверяют БД **и** файловую систему.
- [ ] `ruff`, `mypy`, `tsc --noEmit`, `vitest` — без ошибок.
- [ ] `docker compose down -v && docker compose up --build` → работает с нуля.
