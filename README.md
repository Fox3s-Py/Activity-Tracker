# Activity Tracker

Трекер времени в приложениях: клиент на Windows отслеживает активное окно
(через WinAPI event hooks), шлёт данные на свой REST API (защищённый JWT),
бэкенд хранит и агрегирует их в PostgreSQL, Telegram-бот показывает
статистику с интерактивным drill-down.

Пет-проект для портфолио — стек выбран специально под практику
FastAPI + PostgreSQL + SQLAlchemy + Alembic. Пишу и разбираюсь по ходу —
не просто генерирую код, а стараюсь понимать каждую часть.

## Стек

- **Клиент:** Python, `win32gui`/`win32process` (WinAPI event hooks), `psutil`,
  `requests` (отправка на API), SQLite (локальная очередь на случай
  недоступности сервера)
- **Backend:** FastAPI, PostgreSQL, SQLAlchemy, Alembic, pytest,
  JWT-аутентификация (`python-jose`, `passlib`/`bcrypt`)
- **Бот:** Telegram (aiogram) — статистика за сегодня/вчера/неделю,
  трёхуровневый drill-down (приложение → сайт → конкретная страница)

## Структура

```
backend/
  app/
    main.py         — FastAPI-приложение, все эндпоинты
    database.py      — подключение к PostgreSQL, фабрика сессий (get_db)
    models.py         — SQLAlchemy-модели Activity, User
    schemas.py         — Pydantic-схемы (включая серверную очистку
                          NUL-байтов, UserCreate/UserOut/Token)
    auth.py            — хэширование паролей, создание/проверка JWT,
                          зависимость get_current_user
    routers/
      auth.py            — эндпоинты /auth/register, /auth/login, /auth/me
  alembic/            — миграции схемы БД
  alembic.ini
  scripts/
    create_tables.py   — разовое создание таблиц напрямую из моделей (до Alembic)
    test_connection.py — учебный скрипт проверки подключения к БД
    debug_titles.py     — учебный скрипт: смотрит на реальные window_title
                          из базы побайтово (repr + невидимые Unicode-символы)
  tests/
    conftest.py          — фикстуры: изолированная тестовая БД (SQLite in-memory),
                            auth_headers (регистрация+логин тестового пользователя)
    test_health.py         — /health
    test_activities.py      — POST /activities/batch (валидация, регрессия
                              на NUL-байты, требование авторизации)
    test_stats.py            — /stats/daily, /stats/weekly (агрегация, границы недели)
    test_extract_site.py      — юнит-тесты на extract_site/clean_telegram_title
    test_auth.py               — регистрация, логин, /auth/me, отказ по
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
cp .env.example .env             # прописать DATABASE_URL и SECRET_KEY
python scripts/create_tables.py   # создать схему (первый раз, на пустой БД)
alembic stamp head                 # сообщить Alembic, что БД уже на актуальной версии
uvicorn app.main:app --reload
```

`SECRET_KEY` — сгенерировать своей командой:
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
боевая PostgreSQL не трогается.

### Эндпоинты

**Аутентификация:**
- `POST /auth/register` — регистрация (`username`, `password`)
- `POST /auth/login` — логин (form-data, не JSON — стандарт OAuth2), возвращает JWT-токен
- `GET /auth/me` — данные текущего пользователя по токену (требует авторизации)

**Активность (все требуют заголовок `Authorization: Bearer <токен>`):**
- `GET /health` — проверка живости (без авторизации)
- `POST /activities/batch` — принимает пачку интервалов активности:

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
  приложению за день (по умолчанию — сегодня)
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
- [x] Аутентификация API (JWT): модель `User`, хэширование паролей
      (bcrypt), регистрация/логин, `get_current_user`, защита всех
      бизнес-эндпоинтов; клиент и бот логинятся и переживают истечение
      токена (авто-relogin при 401)
- [ ] CI (GitHub Actions) — автозапуск pytest при каждом пуше в репозиторий,
      без необходимости помнить и запускать тесты руками
- [ ] Мультипользовательский режим: `user_id` в `Activity` (привязка
      события к залогинившемуся пользователю), фильтрация статистики по
      пользователю, ролевая модель доступа (RBAC на основе `User.is_admin`) —
      обычный пользователь видит в боте только свою статистику, админ
      может запросить агрегацию по всем; регистрация второго реального
      пользователя (друга), `uvicorn --host 0.0.0.0` для приёма подключений
      не только с localhost
- [ ] Веб-дашборд (Chart.js)
- [ ] Docker