# Activity Tracker

Трекер времени в приложениях: клиент на Windows отслеживает активное окно
(через WinAPI event hooks), сервер хранит и агрегирует данные, в конце дня
летит сводка в Telegram.

Пет-проект для портфолио — стек выбран специально под практику
FastAPI + PostgreSQL + SQLAlchemy + Alembic. Пишу и разбираюсь по ходу —
не просто генерирую код, а стараюсь понимать каждую часть.

## Стек

- **Клиент:** Python, `win32gui`/`win32process` (WinAPI event hooks), `psutil`, SQLite (локальный буфер)
- **Backend:** FastAPI, PostgreSQL, SQLAlchemy, Alembic
- **Бот (план):** Telegram (aiogram) — сводка в конце дня

## Структура

Пока всё в корне одним плоским проектом (разделение на клиент/бэкенд —
в планах, когда дойдёт до батчевой отправки и Docker):

```
Tracker.py           — клиент: event-driven трекинг активного окна, запись в SQLite
Report.py            — консольный отчёт по локальной SQLite (топ-10 по времени)

main.py              — FastAPI-приложение, эндпоинты
database.py           — подключение к PostgreSQL, фабрика сессий (get_db)
models.py             — SQLAlchemy-модель Activity
schemas.py            — Pydantic-схемы для приёма данных API
create_tables.py       — разовое создание таблиц из моделей (до Alembic)
test_connection.py     — проверка подключения к БД

alembic/              — миграции схемы БД
alembic.ini
```

## Backend — как запустить локально

Нужен локально установленный PostgreSQL (без Docker, пока сознательно).

```bash
pip install -r requirements.txt   # если файла ещё нет — см. ниже
cp .env.example .env               # прописать свой DATABASE_URL
alembic upgrade head               # применить миграции
uvicorn main:app --reload
```

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

## Статус

- [x] Клиент: event-driven трекинг активного окна (WinAPI `SetWinEventHook`)
- [x] Клиент: фильтрация системных окон-мельканий (Alt+Tab и т.п.)
- [x] Клиент: трекинг переключения вкладок в браузере (через смену заголовка)
- [x] Клиент: локальное хранение в SQLite + консольный отчёт (топ-10 по времени)
- [x] Backend: подключение к PostgreSQL, модель `Activity`
- [x] Backend: миграции через Alembic
- [x] Backend: REST API — приём батчей активности (`POST /activities/batch`)
- [ ] Backend: агрегация и отчёты (`GET /stats/daily`, `GET /stats/weekly`)
- [ ] Категоризация приложений (работа/развлечения/и т.д.)
- [ ] Клиент: батчевая отправка на бэкенд + SQLite как fallback-буфер
- [ ] Telegram-бот с дневной сводкой
- [ ] Веб-дашборд (Chart.js)
- [ ] Docker