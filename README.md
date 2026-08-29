# Activity Tracker

Трекер времени в приложениях: клиент на Windows отслеживает активное окно
(через WinAPI event hooks), шлёт данные на свой REST API (защищённый JWT,
с изоляцией данных по пользователю), бэкенд хранит и агрегирует их в
PostgreSQL, Telegram-бот показывает статистику с интерактивным drill-down.

Пет-проект для портфолио — стек выбран специально под практику
FastAPI + PostgreSQL + SQLAlchemy + Alembic. Пишу и разбираюсь по ходу —
не просто генерирую код, а стараюсь понимать каждую часть.

## Стек

- **Клиент:** Python, `win32gui`/`win32process` (WinAPI event hooks), `psutil`,
  `requests` (отправка на API), SQLite (локальная очередь на случай
  недоступности сервера)
- **Backend:** FastAPI, PostgreSQL, SQLAlchemy, Alembic, pytest,
  JWT-аутентификация (`python-jose`, `passlib`/`bcrypt`) — два способа
  входа: логин/пароль и через Telegram
- **Бот:** Telegram (aiogram) — статистика за сегодня/вчера/неделю,
  трёхуровневый drill-down (приложение → сайт → конкретная страница)

## Структура

```text
.github/
  workflows/
    tests.yml           — CI: автозапуск pytest (backend/tests/) при пуше
                          и pull request в main

backend/
  app/
    main.py         — FastAPI-приложение, все эндпоинты, привязка
                       activities к current_user, фильтрация /stats/*
                       по владельцу
    database.py      — подключение к PostgreSQL, фабрика сессий (get_db)
    models.py         — SQLAlchemy-модели Activity (с user_id), User
                          (username/hashed_password + telegram_id — оба
                          способа входа сосуществуют)
    schemas.py         — Pydantic-схемы (включая серверную очистку
                          NUL-байтов, UserCreate/UserOut/Token/
                          TelegramLoginRequest)
    auth.py            — хэширование паролей, создание/проверка JWT,
                          зависимость get_current_user, BOT_SERVICE_SECRET
    routers/
      auth.py            — /auth/register, /auth/login, /auth/telegram-login,
                          /auth/me, /auth/device/start, /auth/device/confirm,
                          /auth/device/poll, /auth/refresh
  alembic/            — миграции схемы БД
  alembic.ini
  scripts/
    create_tables.py   — разовое создание таблиц напрямую из моделей (до Alembic)
    test_connection.py — учебный скрипт проверки подключения к БД
    debug_titles.py     — учебный скрипт: смотрит на реальные window_title
                          из базы побайтово (repr + невидимые Unicode-символы)
    backfill_user_id.py  — разовый скрипт: привязывает старые (докуда
                          появился user_id) записи activities к конкретному
                          пользователю
    link_telegram_id.py  — разовый скрипт: привязывает реальный telegram_id
                          к уже существующему аккаунту (логин/пароль), чтобы
                          не расщеплять историю после перехода на Telegram-вход
    inspect_telegram_state.py — read-only диагностика перед link_telegram_id.py
    delete_empty_user.py — разовый скрипт: безопасное удаление пользователя-
                          дубликата (с проверкой, что у него 0 записей activities)
  tests/
    conftest.py          — фикстуры: изолированная тестовая БД (SQLite in-memory),
                            auth_headers (регистрация+логин тестового пользователя)
    test_health.py         — /health
    test_activities.py      — POST /activities/batch (валидация, регрессия
                              на NUL-байты, требование авторизации,
                              корректная привязка user_id)
    test_stats.py            — /stats/daily, /stats/weekly (агрегация,
                              границы недели, изоляция данных между пользователями)
    test_extract_site.py      — юнит-тесты на extract_site/clean_telegram_title
    test_auth.py               — регистрация, логин, /auth/me, telegram-login
                                 (создание/переиспользование пользователя,
                                 неверный секрет бота), отказ по
                                 неверным/просроченным/отсутствующим токенам
    test_device_flow.py         — device flow целиком: код -> подтверждение ->
                                 опрос -> рабочие токены; код нельзя использовать
                                 дважды; refresh-токен ротируется, старый
                                 перестаёт работать после использования
  pytest.ini
  requirements.txt
  .env.example

client/
  tracker.py          — клиент: event-driven трекинг активного окна,
                         батчевая отправка на API (с JWT-логином и
                         авто-relogin при 401) + SQLite fallback-очередь,
                         штатная остановка командой 'stop' (не Ctrl+C)
  scripts/
    test_send.py        — учебный скрипт: тестовая отправка батча на API
  requirements.txt
  .env.example

bot/
  bot.py               — Telegram-бот: постоянная клавиатура (сегодня/
                          вчера/неделя), inline drill-down по приложениям
                          и сайтам, автоочистка старых сообщений,
                          JWT-логин через authenticated_request(); плюс
                          распознавание 6-символьного кода device flow и
                          его подтверждение на бэкенде
  requirements.txt
  .env.example
```

## Backend — как запустить локально

Нужен локально установленный PostgreSQL (без Docker, пока сознательно).

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env             # прописать DATABASE_URL, SECRET_KEY, BOT_SERVICE_SECRET
python scripts/create_tables.py   # создать схему (первый раз, на пустой БД)
alembic stamp head                 # сообщить Alembic, что БД уже на актуальной версии
uvicorn app.main:app --reload
```

`SECRET_KEY`/`BOT_SERVICE_SECRET` — сгенерировать своей командой:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> Первая миграция в истории пустая (таблица создавалась раньше через
> `create_tables.py` ещё до подключения Alembic) — поэтому `alembic upgrade head`
> на чистой базе не создаст таблицу. Правильный порядок для свежего клонирования —
> выше: сначала `create_tables.py`, потом `stamp head`. Дальше, при любых новых
> изменениях моделей — уже обычный цикл `alembic revision --autogenerate` +
> `alembic upgrade head`.

Документация API (Swagger UI): `http://127.0.0.1:8000/docs` — кнопка
**Authorize** позволяет залогиниться прямо в браузере и тестировать
защищённые эндпоинты без ручной вставки токена в заголовки.

### Тесты

```bash
cd backend
pytest -v
```

Гоняются на изолированной SQLite in-memory базе (см. `tests/conftest.py`) —
боевая PostgreSQL не трогается. Секреты (`BOT_SERVICE_SECRET` и т.п.)
внутри тестов подменяются через `monkeypatch`, а не читаются из реального
`.env` — тесты не зависят от того, что стоит в системе конкретного разработчика.

**Текущее покрытие `backend/app/` — 89% (32 теста), но цифра льстит:**
часть тестов ложно-зелёные из-за расхождений SQLite/PostgreSQL (например,
NUL-байты отвергает только Postgres), самый сложный эндпоинт проекта
(`/stats/daily/breakdown`) не покрыт вообще, а `bot/bot.py` и
`client/tracker.py` (почти 900 строк суммарно) — 0% тестов, потому что
их сейчас невозможно импортировать в CI (`ctypes.windll`/`aiogram.Bot`
на уровне модуля). Полный аудит и план закрытия — заведён отдельными
GitHub Issues, закрываются по порядку: сначала независимые багфиксы и
дыры в security-покрытии, затем `/stats/daily/breakdown`, затем Postgres
в CI, затем рефакторинг client/bot на тестируемую чистую логику, затем
тесты клиента/бота, и в конце — порог покрытия в CI.

### Эндпоинты

**Аутентификация — два независимых способа входа:**

- `POST /auth/register` — регистрация по логину/паролю (`username`, `password`)
- `POST /auth/login` — логин по логину/паролю (form-data, не JSON —
  стандарт OAuth2), возвращает JWT-токен
- `POST /auth/telegram-login` — логин через Telegram: `telegram_id` +
  `bot_secret` (общий секрет между ботом и бэкендом, не пароль
  конкретного человека). Пользователь создаётся автоматически при первом
  обращении; повторный вызов с тем же `telegram_id` не плодит дубликат
- `GET /auth/me` — данные текущего пользователя по токену (требует авторизации)

**Device flow — вход десктоп-клиента через Telegram, без пароля:**

- `POST /auth/device/start` — клиент запрашивает одноразовый код (6 символов,
  без похожих на глаз O/0/I/1), живёт 10 минут
- `POST /auth/device/confirm` — вызывается ботом, когда пользователь прислал
  код в Telegram; `bot_secret` подтверждает, что запрос реально от бота
- `POST /auth/device/poll` — клиент опрашивает раз в пару секунд; пока код не
  подтверждён — `{"status": "pending"}`, как только подтверждён — отдаёт
  `access_token` + `refresh_token` и "сжигает" код (повторный poll тем же
  кодом → 404)
- `POST /auth/refresh` — обмен `refresh_token` на новую пару токенов
  (ротация: старый `refresh_token` перестаёт работать сразу после обмена).
  `refresh_token` в БД хранится не как есть, а в виде sha256-хэша — та же
  логика, что и с паролем

**Активность (все требуют заголовок `Authorization: Bearer <токен>`,
данные видны только тому, кто их создал):**

- `GET /health` — проверка живости (без авторизации)
- `POST /activities/batch` — принимает пачку интервалов активности,
  привязывает их к `current_user`:

```json
{
  "events": [
    {
      "process_name": "chrome.exe",
      "window_title": "YouTube",
      "started_at": "2026-08-20T14:00:00",
      "ended_at": "2026-08-20T14:05:00",
      "duration_seconds": 300.0
    }
  ]
}
```

- `GET /stats/daily?target_date=YYYY-MM-DD` — сумма времени по каждому
  приложению за день у текущего пользователя (по умолчанию — сегодня)
- `GET /stats/weekly?target_date=YYYY-MM-DD` — то же самое за неделю
  (понедельник-воскресенье, в которую попадает переданная дата)
- `GET /stats/daily/breakdown?process_name=chrome.exe&site=YouTube&date_from=...&date_to=...` —
  drill-down внутри одного приложения за диапазон дат: без `site` — топ
  "сайтов" (вытащены из `window_title`), с `site` — топ конкретных
  заголовков внутри него. `date_from`/`date_to` — по умолчанию сегодня;
  задаются диапазоном, а не одной датой, чтобы этот же эндпоинт одинаково
  работал и для дневной, и для недельной детализации

## Client — как запустить локально

Требует Windows (WinAPI-хуки не кроссплатформенные).

```bash
cd client
pip install -r requirements.txt
cp .env.example .env    # прописать API_URL, TRACKER_USERNAME, TRACKER_PASSWORD
python tracker.py
```

Логинится на бэкенд при первой отправке, хранит токен в памяти; если
сервер отклонит токен (401) — логинится заново и повторяет отправку.
Отправляет накопленные интервалы на бэкенд раз в 5 минут. Если бэкенд
недоступен (или логин не проходит) — сохраняет их в локальную
SQLite-очередь (`pending_events.db`) и досылает при следующей успешной попытке.

Останавливается командой `stop` + Enter в той же консоли (не через Ctrl+C —
он намеренно отключён, чтобы не ронять WinAPI event hook грязным исключением).

## Bot — как запустить локально

```bash
cd bot
pip install -r requirements.txt
cp .env.example .env    # TELEGRAM_BOT_TOKEN, API_URL, BOT_SERVICE_SECRET
python bot.py
```

Бэкенд должен быть запущен. Каждый написавший боту получает **свой**
токен и своего backend-пользователя через `/auth/telegram-login`
(`telegram_id` берётся напрямую из Telegram — подделать нельзя,
`BOT_SERVICE_SECRET` доказывает бэкенду, что запрос реально от нашего
бота). Токены хранятся в памяти по одному на каждого telegram-пользователя
(`_tokens: dict[int, str]`), переиспользуются, при 401 — перелогин и
повтор (единая точка `authenticated_request(telegram_id, ...)`). `/start`
показывает постоянную клавиатуру с тремя кнопками (сегодня / вчера /
неделя). Тап по приложению в статистике открывает детализацию по сайтам,
тап по сайту — детализацию по конкретным страницам. При выборе нового
периода старые сообщения бота автоматически удаляются, чтобы чат не
захламлялся.

## Статус

- [x] Клиент: event-driven трекинг активного окна (WinAPI `SetWinEventHook`)
- [x] Клиент: фильтрация системных окон-мельканий (Alt+Tab и т.п.)
- [x] Клиент: трекинг переключения вкладок в браузере (через смену заголовка)
- [x] Клиент: батчевая отправка на бэкенд + SQLite fallback-очередь при
      недоступности сервера
- [x] Клиент: штатная остановка (команда 'stop', без грязного Ctrl+C)
- [x] Backend: подключение к PostgreSQL, модель `Activity`
- [x] Backend: миграции через Alembic
- [x] Backend: REST API — приём батчей активности (`POST /activities/batch`)
- [x] Backend: серверная валидация данных (очистка NUL-байтов на границе API)
- [x] Backend: агрегация по дню и неделе (`GET /stats/daily`, `GET /stats/weekly`)
- [x] Backend: drill-down по сайтам внутри приложения, диапазон дат
      (`GET /stats/daily/breakdown`)
- [x] Backend: очистка "мусора" из заголовков Telegram (варианты тире,
      невидимые Unicode-символы, счётчики непрочитанных) для честной
      агрегации по собеседникам
- [x] Telegram-бот: постоянная клавиатура, трёхуровневый drill-down
      (приложение → сайт → страница), период прокидывается через все
      уровни, устойчивость к сетевым сбоям Telegram
- [x] Backend: автотесты (pytest) на эндпоинты — `/health`, `/activities/batch`
      (включая регрессию на NUL-байты), `/stats/daily`, `/stats/weekly`
- [x] Backend: юнит-тесты на чистые функции (`extract_site`,
      `clean_telegram_title`) — все найденные источники мусора + edge cases
- [x] Аутентификация API (JWT) по логину/паролю: модель `User`,
      хэширование паролей (bcrypt), регистрация/логин, `get_current_user`,
      защита всех бизнес-эндпоинтов; клиент и бот логинятся и переживают
      истечение токена (авто-relogin при 401)
- [x] Мультипользовательский режим (backend): `user_id` в `Activity`
      (Alembic-миграция + разовый backfill старых данных через
      `scripts/backfill_user_id.py`), `POST /activities/batch` привязывает
      события к `current_user`, `/stats/*` фильтруют по владельцу —
      изоляция данных между пользователями подтверждена тестами
- [x] Аутентификация через Telegram (`telegram_id`, `/auth/telegram-login`,
      `BOT_SERVICE_SECRET`) как альтернативный способ входа — фундамент
      для персональной идентификации в боте
- [x] Бот: каждый пишущий в Telegram получает СВОЙ токен и своего
      backend-пользователя через `/auth/telegram-login`
      (`_tokens: dict[telegram_id, token]`) — реальное "у каждого своя
      статистика в боте" подтверждено живым тестом с двумя аккаунтами
- [x] Привязка старой истории (7467 записей) к реальному `telegram_id`
      основного аккаунта — история не расщепилась на два разных аккаунта
      после перехода на Telegram-логин в боте
- [ ] Ролевая модель доступа (RBAC на основе `User.is_admin`) — админ
      может запросить агрегацию по всем пользователям в боте
- [x] Device flow: авторизация десктоп-клиента через Telegram — одноразовый
      код (`/auth/device/start`) → подтверждение через бота
      (`/auth/device/confirm`) → опрос сервера клиентом (`/auth/device/poll`)
      → рабочие токены. Refresh-токен хранится как sha256-хэш (не как есть),
      сравнение через `hmac.compare_digest`, ротируется при каждом
      `/auth/refresh` — старый сразу перестаёт работать. Покрыто тестами
      (9 сценариев, включая намеренную поломку "сжигания" кода и ротации,
      чтобы убедиться, что тесты реально их ловят), плюс ручная сквозная
      проверка через бота живьём
- [ ] Трей-приложение (`pystray`/`tkinter`), настройки (интервал отправки,
      локальный/VPS-режим, автозапуск), сборка в `.exe` (PyInstaller),
      релиз на GitHub
- [x] CI (GitHub Actions) — `.github/workflows/tests.yml`, автозапуск
      `pytest tests/` при пуше/PR в main; подтверждено живым зелёным
      прогоном на GitHub
- [ ] Полировка тестового покрытия до полного доверия (backend 89% с
      оговорками → честные 100%; `bot/bot.py`/`client/tracker.py` 0% →
      покрыты после рефакторинга на тестируемую логику) — план из 16
      пунктов, заведён отдельными GitHub Issues, закрывается по порядку
- [ ] Веб-дашборд (Chart.js)
- [ ] Docker
- [ ] VPS-хостинг + HTTPS (в самом конце — после того как всё остальное
      обкатано локально/по Radmin VPN)
