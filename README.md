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

```
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
      auth.py            — /auth/register, /auth/login, /auth/telegram-login, /auth/me
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
                          JWT-логин через authenticated_request()
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
cp .env.example .env    # TELEGRAM_BOT_TOKEN, API_URL, BOT_USERNAME, BOT_PASSWORD
python bot.py
```

Бэкенд должен быть запущен. Логинится на бэкенд лениво (при первом
запросе от пользователя), переиспользует токен, при 401 — перелогин и
повтор (единая точка `authenticated_request()`). `/start` показывает
постоянную клавиатуру с тремя кнопками (сегодня / вчера / неделя). Тап по
приложению в статистике открывает детализацию по сайтам, тап по сайту —
детализацию по конкретным страницам. При выборе нового периода старые
сообщения бота автоматически удаляются, чтобы чат не захламлялся.

> Сейчас бот логинится ОДНИМ общим аккаунтом (`BOT_USERNAME`/`BOT_PASSWORD`)
> независимо от того, кто ему пишет в Telegram — реальное разделение "у
> каждого своя статистика" ещё не готово, см. roadmap ниже.

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
- [ ] Бот: связать каждого пишущего в Telegram с его собственным
      backend-аккаунтом через `/auth/telegram-login` вместо одного общего
      логина — реальное "у каждого своя статистика в боте"
- [ ] Ролевая модель доступа (RBAC на основе `User.is_admin`) — админ
      может запросить агрегацию по всем пользователям в боте
- [ ] Device flow: авторизация десктоп-клиента через Telegram (код +
      диплинк + опрос сервера), долгоживущие/refresh-токены для фонового
      приложения
- [ ] Трей-приложение (`pystray`/`tkinter`), настройки (интервал отправки,
      локальный/VPS-режим, автозапуск), сборка в `.exe` (PyInstaller),
      релиз на GitHub
- [ ] CI (GitHub Actions) — автозапуск pytest при каждом пуше в репозиторий
- [ ] Веб-дашборд (Chart.js)
- [ ] Docker
- [ ] VPS-хостинг + HTTPS (в самом конце — после того как всё остальное
      обкатано локально/по Radmin VPN)