# Activity Tracker

Трекер времени в приложениях: клиент на Windows отслеживает активное окно
(через WinAPI event hooks), шлёт данные на свой REST API, бэкенд хранит и
агрегирует их в PostgreSQL. В планах — дневная сводка в Telegram.

Пет-проект для портфолио — стек выбран специально под практику
FastAPI + PostgreSQL + SQLAlchemy + Alembic. Пишу и разбираюсь по ходу —
не просто генерирую код, а стараюсь понимать каждую часть.

## Стек

- **Клиент:** Python, `win32gui`/`win32process` (WinAPI event hooks), `psutil`,
  `requests` (отправка на API), SQLite (локальная очередь на случай
  недоступности сервера)
- **Backend:** FastAPI, PostgreSQL, SQLAlchemy, Alembic
- **Бот (план):** Telegram (aiogram) — дневная сводка, drill-down статистика

## Структура

```
backend/
  app/
    main.py         — FastAPI-приложение, все эндпоинты
    database.py      — подключение к PostgreSQL, фабрика сессий (get_db)
    models.py         — SQLAlchemy-модель Activity
    schemas.py         — Pydantic-схемы для приёма данных API
  alembic/            — миграции схемы БД
  alembic.ini
  scripts/
    create_tables.py   — разовое создание таблиц напрямую из моделей (до Alembic)
    test_connection.py — учебный скрипт проверки подключения к БД
  requirements.txt
  .env.example

client/
  tracker.py          — клиент: event-driven трекинг активного окна,
                         батчевая отправка на API + SQLite fallback-очередь
  report.py            — консольный топ-10 (СЕЙЧАС НЕАКТУАЛЕН: смотрит
                         в старую локальную SQLite, а не в Postgres —
                         данные там больше не обновляются; решение о
                         будущем файла пока отложено)
  scripts/
    test_send.py        — учебный скрипт: тестовая отправка батча на API
  requirements.txt

bot/                  — пока пусто, следующий этап
```

## Backend — как запустить локально

Нужен локально установленный PostgreSQL (без Docker, пока сознательно).

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env             # прописать свой DATABASE_URL
python scripts/create_tables.py   # создать схему (первый раз, на пустой БД)
alembic stamp head                 # сообщить Alembic, что БД уже на актуальной версии
uvicorn app.main:app --reload
```

> Первая миграция в истории пустая (таблица создавалась раньше через
> `create_tables.py` ещё до подключения Alembic) — поэтому `alembic upgrade head`
> на чистой базе не создаст таблицу. Правильный порядок для свежего клонирования —
> выше: сначала `create_tables.py`, потом `stamp head`. Дальше, при любых новых
> изменениях моделей — уже обычный цикл `alembic revision --autogenerate` +
> `alembic upgrade head`.

Документация API (Swagger UI): `http://127.0.0.1:8000/docs`

### Эндпоинты

- `GET /health` — проверка живости
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
- `GET /stats/daily/breakdown?process_name=chrome.exe&site=YouTube` —
  drill-down внутри одного приложения: без `site` — топ "сайтов" (вытащены
  из `window_title`), с `site` — топ конкретных заголовков внутри него.
  Точность парсинга сайта пока не идеальна — см. открытый issue

## Client — как запустить локально

Требует Windows (WinAPI-хуки не кроссплатформенные).

```bash
cd client
pip install -r requirements.txt
python tracker.py
```

Отправляет накопленные интервалы на бэкенд раз в 5 минут. Если бэкенд
недоступен — сохраняет их в локальную SQLite-очередь (`pending_events.db`)
и досылает при следующей успешной попытке.

## Статус

- [x] Клиент: event-driven трекинг активного окна (WinAPI `SetWinEventHook`)
- [x] Клиент: фильтрация системных окон-мельканий (Alt+Tab и т.п.)
- [x] Клиент: трекинг переключения вкладок в браузере (через смену заголовка)
- [x] Клиент: батчевая отправка на бэкенд + SQLite fallback-очередь при
      недоступности сервера
- [x] Backend: подключение к PostgreSQL, модель `Activity`
- [x] Backend: миграции через Alembic
- [x] Backend: REST API — приём батчей активности (`POST /activities/batch`)
- [x] Backend: агрегация по дню и неделе (`GET /stats/daily`, `GET /stats/weekly`)
- [x] Backend: drill-down по сайтам внутри приложения (`GET /stats/daily/breakdown`)
      — черновая точность, см. issue
- [ ] Telegram-бот: сводка + интерактивный drill-down (топ приложений →
      топ сайтов → топ конкретных страниц)
- [ ] Определиться с судьбой `Report.py` (переписать под API или убрать)
- [ ] Мультипользовательский режим: `user_id`/`device_id` в `Activity`,
      конфигурируемый `API_URL` на клиенте, `uvicorn --host 0.0.0.0`,
      ролевая модель доступа (RBAC) — обычный пользователь видит в боте
      только свою статистику, админ может запросить агрегацию по всем
- [ ] Аутентификация API (JWT) — сейчас любой клиент может писать/читать
      данные без проверки, кто он такой; естественно ложится поверх
      будущего мультипользовательского режима выше
- [ ] Автотесты (pytest) — пока всё проверялось руками через Swagger;
      добавить хотя бы базовые тесты на эндпоинты (`/health`,
      `/activities/batch`, агрегации) прежде чем структура API разрастётся
- [ ] Веб-дашборд (Chart.js)
- [ ] Docker