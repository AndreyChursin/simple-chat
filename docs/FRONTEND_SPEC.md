# FRONTEND SPEC — UI/UX (Section 4, Appendix A)

Стек: React 18 + Vite + TypeScript (strict), react-router v6, TanStack Query (REST), Zustand (сокет, presence, unread, typing), react-virtuoso (лента), @emoji-mart/react. Без UI-фреймворков-монстров; CSS Modules + CSS-переменные (светлая/тёмная тема по `prefers-color-scheme`).

## 1. Маршруты

| Путь | Экран | Доступ |
|---|---|---|
| `/login` | Sign in | гость |
| `/register` | Sign up | гость |
| `/forgot-password` | Запрос сброса | гость |
| `/reset-password?token=` | Новый пароль | гость |
| `/` | Main Chat Layout, пустое состояние (ничего не открыто) | auth |
| `/rooms/:roomId` | Комната | auth + участник |
| `/dm/:dmId` | Личный диалог | auth + сторона |
| `/rooms/catalog` | Public Rooms Catalog | auth |
| `/rooms/private` | Private Rooms (мои приватные + входящие приглашения) | auth |
| `/contacts` | Contacts (друзья, заявки, баны) | auth |
| `/sessions` | Active sessions | auth |
| `/profile` | Profile / смена пароля / удаление аккаунта | auth |

Неавторизованный доступ к auth-маршруту → `/login?next=...`. `401` от любого запроса или WS close `4001` → сброс стора, редирект на `/login`.

## 2. Верхнее меню (всегда видно на auth-экранах)

```
[Logo] | Public Rooms | Private Rooms | Contacts | Sessions | Profile ▼ | Sign out
```
- `Logo` → `/`.
- `Public Rooms` → каталог; `Private Rooms` → список приватных комнат + бейдж числа входящих приглашений; `Contacts` → бейдж числа входящих заявок в друзья.
- `Profile ▼` — dropdown: имя (`username`, `display_name`), `Profile settings`, `Change password`, `Delete account`.
- `Sign out` — `POST /api/auth/logout`, затем `/login`. Завершает только текущую сессию; у других вкладок **этой же** сессии WS закрывается с `4001` и они тоже переходят на `/login` (это та же cookie). Другие браузеры не затрагиваются.

## 3. Auth-экраны
- **Sign in:** email, password, чекбокс `Keep me signed in` (по умолчанию выключен), ссылки `Forgot password?`, `Create account`. Ошибка `401` → «Invalid email or password» (без уточнения).
- **Sign up:** email, username (подсказка: «3–32 символа: буквы, цифры, `_ . -`. **Username нельзя будет изменить**»), password + confirm. Ошибки `409` — под соответствующим полем.
- **Forgot password:** email → всегда сообщение «If the account exists, we sent a link» (в dev письмо в Mailpit http://localhost:8025).
- **Reset password:** новый пароль + подтверждение; `410` → «Link expired or already used» + ссылка на повторный запрос.

## 4. Main Chat Layout

### 4.1. Сетка и согласование 4.1.1 с Appendix A

ТЗ 4.1.1 говорит: «Rooms and contacts are displayed on the right. After entering a room, the room list becomes compacted in accordion style. Room members are shown on the right side». Вайрфрейм Appendix A показывает отдельную сворачиваемую навигационную панель и правую панель контекста комнаты. Решение — **трёхколоночная сетка на CSS grid-template-areas**, где навигация и контекст — разные колонки, а поведение аккордеона из 4.1.1 реализовано внутри навигации:

```
┌──────────────────────────── Top menu ─────────────────────────────┐
├───────────────┬───────────────────────────────────┬───────────────┤
│ NAV SIDEBAR   │ CHAT WINDOW                        │ CONTEXT PANEL │
│ (collapsible) │  header: #room · description       │ (только когда │
│ [search…]     │  ┌───────────────────────────────┐ │  открыт чат)  │
│ ▾ Public  (3) │  │ messages (virtualized)        │ │ Room info     │
│    #general 2 │  │                               │ │ Owner         │
│    #dev       │  └───────────────────────────────┘ │ Admins        │
│ ▸ Private (1) │  Replying to: … ✕                  │ Members       │
│ ▾ Contacts    │  [😊][📎][ multiline textarea ][Send]│  ● online     │
│    ● alice 1  │                                    │  ◐ AFK        │
│    ◐ bob      │                                    │  ○ offline    │
│ [+ Create room]│                                   │[Invite user]  │
│               │                                    │[Manage room]  │
└───────────────┴───────────────────────────────────┴───────────────┘
```

Правила, снимающие конфликт:
1. **Пустое состояние (`/`, чат не открыт):** правая панель контекста скрыта; навигация в режиме *expanded* — все секции (Public Rooms, Private Rooms, Contacts) раскрыты полностью. Центр — приветствие + быстрые ссылки (каталог, создать комнату, добавить друга).
2. **После входа в комнату:** навигация переходит в режим *compact accordion* — раскрыта только секция текущего чата, остальные свёрнуты до заголовка с суммарным бейджем непрочитанного (`▸ Private (1)`). Пользователь может вручную раскрыть любую секцию (состояние запоминается в `localStorage` на сессию работы). Справа появляется панель контекста со списком участников и их статусами — выполняется «Room members are shown on the right side».
3. **DM:** правая панель показывает карточку собеседника (presence, кнопки `Remove friend`, `Ban user`, статус frozen) вместо списка участников.
4. Сторона навигационной панели задаётся одной CSS-переменной/grid-areas (`"nav chat ctx"`) — если заказчик потребует навигацию справа (буквальное прочтение 4.1.1), меняется только `grid-template-areas: "chat ctx nav"` без переделки компонентов.
5. Адаптивность: ≥ 1280 px — три колонки (nav 260 px, ctx 280 px); 960–1279 — панель контекста становится выдвижной (кнопка 👥 в заголовке чата); < 960 — навигация тоже выдвижная (кнопка ☰). Кнопка сворачивания nav (`«`) сворачивает её до узкой полосы иконок с бейджами.

### 4.2. Nav Sidebar
- **Поиск** (фильтрует локально комнаты и контакты по подстроке; `Enter` с непустым запросом, не найденным локально → переход в каталог с этим запросом).
- **Public Rooms / Private Rooms** — аккордеон; элемент: `#name`, бейдж непрочитанного (`1…99`, `99+`), жирный шрифт при `unread > 0`, подсветка активного.
- **Contacts** — друзья: точка статуса (● зелёная `online`, ◐ жёлтая `AFK`, ○ серая `offline`, обновление по `presence_update` без перезапроса), username, бейдж непрочитанного DM. Ниже, свернуто — «Past conversations» (frozen DM, иконка 🔒).
- **Create room** — модалка: name (3–64, проверка `409 room_name_taken` под полем), description, переключатель Public/Private.
- Порядок комнат: по времени последнего сообщения (по `new_message`), затем по имени.

### 4.3. Chat Window

**Header:** имя комнаты/собеседника, описание (усечённое, полный текст в title), число участников, кнопка 🔍 (поиск по истории — панель результатов, клик → `messages/around/{id}` с подсветкой), кнопка 👥 на узких экранах. Для DM — presence собеседника.

**Лента сообщений** (react-virtuoso):
- Хронологический порядок сверху вниз, группировка подряд идущих сообщений одного автора в пределах 5 минут, разделители дат.
- Элемент: аватар-инициалы, username (удалённый — «Deleted user» курсивом), время (title — полная дата), текст с сохранением переносов (`white-space: pre-wrap`, `overflow-wrap: anywhere`), авто-ссылки (http/https, `rel="noopener noreferrer"`), **без рендера HTML/Markdown** (только текст — защита от XSS), серая пометка `edited` при `edited_at`. Удалённое — серый курсив «Message deleted».
- Цитата ответа: блок с левой полосой, автор + первые ~200 символов; клик — прокрутка к оригиналу (загрузка `around`, если не в памяти); если оригинал удалён — «Original message deleted».
- Вложения: изображения — превью (`max-height: 320px`, место резервируется по `width/height` чтобы не прыгал скролл), клик → лайтбокс; файлы — карточка (иконка, `original_filename`, размер, кнопка download). Комментарий к вложению — подписью.
- Hover-меню сообщения: `Reply`, `Edit` (только своё), `Delete` (своё; в комнате — любое для admin/owner), `Copy text`. Подтверждение удаления. В frozen DM меню содержит только `Copy text`.

**Умный автоскролл:**
- Считаем пользователя «внизу», если до низа ≤ 80 px (`atBottomThreshold`).
- Новое сообщение: если внизу **или** это моё собственное сообщение → плавная прокрутка вниз; иначе — **не двигать** ленту, показать плашку «↓ N new messages» (клик → вниз).
- При открытии чата: если есть непрочитанные — прокрутка к первому непрочитанному с разделителем «New messages»; иначе — в самый низ.

**Infinite scroll вверх:**
- Начальная загрузка `limit=50`; при приближении к верху (`startReached`) — `GET messages?before_id=<min_id>&limit=50`, пока `has_more`. Позиция скролла сохраняется (virtuoso `firstItemIndex`, стартовое значение 1 000 000 с уменьшением на число подгруженных).
- В памяти держим до 2000 сообщений на чат; при превышении обрезаем самые новые/старые вне вьюпорта (виртуализация гарантирует плавность при 10 000+).
- Индикатор загрузки сверху; «Beginning of conversation» при `has_more=false`.

**Отметка о прочтении:** когда последнее сообщение видно и вкладка видима (`document.visibilityState === 'visible'`) → `mark_read` (debounce 500 мс). Бейдж чата сбрасывается локально сразу, остальные вкладки — по `unread_update`.

**Typing:** строка «bob is typing…» над композером (исчезает через 5 с без новых событий).

### 4.4. Composer
- Multiline `textarea` (auto-grow до 10 строк, затем скролл). `Enter` — отправить, `Shift+Enter` — новая строка, `Esc` — отмена ответа/редактирования. `↑` в пустом поле — редактировать последнее своё сообщение.
- **Счётчик байт:** при > 2800 байт показывается `N / 3072 bytes`; > 3072 — счётчик красный, `Send` заблокирован, подсказка «Message is too long (max 3 KB)». Подсчёт — `new TextEncoder().encode(text).length`.
- **Emoji:** кнопка 😊 → emoji-mart popover, вставка в позицию курсора.
- **Вложения:** кнопка 📎 (`<input type=file multiple>`), **вставка из буфера `Ctrl+V`** (`onPaste`: берём `clipboardData.files`/`items` с `kind === 'file'`; текст вставляется как обычно), drag-and-drop на область чата.
  - Клиентская предпроверка: файл > 20 МБ → ошибка «File exceeds 20 MB», изображение (`type` начинается с `image/`) > 3 МБ → «Image exceeds 3 MB»; не загружается. Сервер всё равно перепроверяет и может вернуть `413` (показываем текст ошибки на чипе).
  - Каждое вложение — чип над полем ввода: превью/иконка, имя, размер, прогресс загрузки (XHR `upload.onprogress`), поле «Add comment», ✕ (отмена: abort + `DELETE /api/attachments/{id}`).
  - Загрузка начинается сразу после выбора/вставки; `Send` активен, когда все загрузки завершены. Максимум 10 вложений.
- **Reply:** `Replying to: <username> — <первые 80 символов>` с ✕ над полем ввода.
- **Edit:** режим редактирования с плашкой «Editing message» и ✕; сохранение — `PATCH`.
- **Отправка:** по WS `send_message` с `client_msg_id`; сообщение сразу появляется в ленте как «sending» (полупрозрачное), заменяется по `ack`/`new_message`; при `error` — красная отметка с `Retry`/`Delete`. Если сокет закрыт — в очередь.
- **Frozen DM:** вместо composer — плашка 🔒 «This conversation is read-only» + причина («You are no longer friends» / «One of you has blocked the other» / «User deleted their account»).
- **Потеря доступа** (`room_access_revoked`): чат закрывается, тост «You were banned from #room» / «Room #room was deleted», комната удаляется из навигации, переход на `/`, кэш сообщений и превью вложений этой комнаты очищается.

### 4.5. Context Panel (комната)
- **Room info:** имя, описание, visibility (🌐/🔒), дата создания.
- **Owner** (с presence), **Admins**, **Members** — с точками статусов, сортировка online → AFK → offline, виртуализированный список (до 1000), поиск по участникам при > 50.
- Клик по участнику → popover: профиль, `Send friend request` (с полем приветствия), `Message` (если друзья), а для admin/owner — `Make admin` (owner), `Remove admin`, `Ban from room`.
- Кнопки **`Invite user`** (все участники; модалка с поиском по username) и **`Manage room`** (только admin/owner). Для обычного участника — `Leave room`; для owner вместо `Leave room` подсказка «Owner can't leave — delete the room in Manage room → Settings».

### 4.6. Модальное окно `Manage Room` (admin/owner)

| Вкладка | Содержимое и действия | Кто видит/может |
|---|---|---|
| `[Members]` | Таблица: user, presence, role, joined_at; поиск. Действие `Remove (ban)` с подтверждением «Removing a member bans them until unbanned» и опциональной причиной | admin, owner (owner — неуязвим, кнопки скрыты) |
| `[Admins]` | Список админов; `Add admin` (выбор из участников) — только owner; `Remove admin` — owner и admin (кроме строки owner, отмечена «Owner») | admin видит, owner — полный контроль |
| `[Banned users]` | Таблица: user, **banned_by**, **banned_at**, reason; `Unban`; `Ban user by username` | admin, owner |
| `[Invitations]` | Pending-приглашения: invitee, inviter, дата; `Revoke`; форма `Invite` | admin, owner |
| `[Settings]` | name, description, visibility (Public/Private) — редактирует owner, у admin поля read-only. **Danger zone:** `Delete room` — подтверждение вводом имени комнаты; предупреждение «All messages, files and images will be permanently deleted» | owner (удаление) |

Все действия обновляют таблицы оптимистично и подтверждаются WS-событиями (`member_removed`, `member_role_changed`, …).

## 5. Public Rooms Catalog (`/rooms/catalog`)
- Поиск (debounce 300 мс) по названию и описанию, бесконечная прокрутка (`cursor`).
- Карточка: `#name`, описание (2 строки), число участников, кнопка `Join` / `Open` (если участник) / «Banned» (disabled, если `is_banned`).
- Join → `POST join` → переход в комнату. `403 room_banned` → тост «You are banned from this room».

## 6. Private Rooms (`/rooms/private`)
- Мои приватные комнаты (с бейджами) + блок **Invitations**: комната, описание, пригласивший, `Accept` / `Decline`. Новые приглашения приходят по `invitation_received` (тост + бейдж в меню).

## 7. Contacts (`/contacts`)
- Вкладки: **Friends** (presence, `Message`, `Remove friend`, `Ban`), **Requests** (Incoming: from, текст приветствия, `Accept`/`Decline`; Outgoing: `Cancel`), **Add friend** (по username + приветственное сообщение), **Blocked** (кого я забанил, `Unban`), **Past conversations** (frozen DM).
- `Ban` — подтверждение: «They won't be able to contact you. Your friendship will end and your conversation will become read-only».

## 8. Sessions (`/sessions`)
- Таблица: браузер + ОС (иконка устройства), IP, создана, последняя активность, «Keep signed in» ✓, метка **This device** для текущей.
- `Sign out` на строке — `DELETE /api/sessions/{id}` (для текущей — как общий Sign out). Кнопка `Sign out all other sessions`.

## 9. Profile (`/profile`)
- Email (read-only), **username (read-only, с подписью «Username cannot be changed»)**, display name (редактируемо).
- **Change password:** current, new, confirm; после успеха тост «Other sessions were signed out».
- **Delete account:** danger zone; модалка перечисляет последствия: «Your account will be deleted; rooms you own (N) will be deleted with all messages and files; you will be removed from other rooms; your messages in other rooms will remain as "Deleted user"» + ввод пароля.

## 10. Клиентская архитектура
- `src/api/` — типизированные функции REST (типы из API_AND_WS_SPEC.md, общий `ApiError` с `code`).
- `src/ws/socket.ts` — единственный WS на вкладку: reconnect с backoff, `heartbeat` 25 с, трекер активности (throttle 10 с), очередь исходящих, роутинг событий в сторы и инвалидацию TanStack Query.
- Сторы Zustand: `presenceStore` (`Map<userId, Presence>`), `unreadStore` (`Map<chatKey, number>`), `typingStore`, `messagesStore` (по чатам: массив, `hasMore`, pending-сообщения).
- `document.title` = `(N) Chat`, где N — сумма непрочитанного; favicon с точкой при N > 0.
- Тесты (vitest + Testing Library): байтовый счётчик/лимиты, логика автоскролла (внизу/не внизу), обработка paste, аккордеон nav (expanded ↔ compact), видимость действий Manage Room по ролям, frozen composer.
