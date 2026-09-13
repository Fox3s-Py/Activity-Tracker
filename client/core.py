"""
client/core.py — чистая логика клиента: фильтрация системных окон, буфер
в памяти, локальная SQLite-очередь неотправленного, отправка на backend.

Намеренно БЕЗ ctypes/win32gui/psutil — модуль не трогает WinAPI ни в одной
строке, поэтому импортируется и тестируется где угодно, включая CI, где
WinAPI физически не существует.
"""

import os
import sqlite3
import threading
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# --- Конфиг ---

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
TRACKER_USERNAME = os.getenv("TRACKER_USERNAME")
TRACKER_PASSWORD = os.getenv("TRACKER_PASSWORD")
REQUEST_TIMEOUT_SECONDS = 5
# Сервер принимает до 1000 событий за запрос (ActivityBatchIn.max_length,
# issue #21) — берём с запасом, а не впритык к потолку: если backend
# когда-нибудь снизит лимит, клиент не застрянет снова.
BATCH_LIMIT = 500

# --- Локальная SQLite-очередь неотправленного ---

PENDING_DB_FILE = Path("pending_events.db")


def init_pending_db() -> None:
    """
    Таблица-очередь: сюда падают события, которые не удалось отправить.

    Соединение открывается и закрывается вручную (try/finally), а не через
    `with sqlite3.connect(...) as conn:` — контекстный менеджер sqlite3.Connection
    управляет только ТРАНЗАКЦИЕЙ (коммит/откат), а не самим соединением, в
    отличие от большинства других контекстных менеджеров (например, open()).
    Без явного close() соединение остаётся открытым до сборки мусора —
    источник ResourceWarning "unclosed database" при активном создании
    много соединений подряд (проявилось в тестах с частыми вызовами очереди).
    """
    conn = sqlite3.connect(PENDING_DB_FILE)
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    process_name TEXT NOT NULL,
                    window_title TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL
                )
                """
            )
    finally:
        conn.close()


def save_pending(events: list[dict]) -> None:
    """Сохраняет события, которые не удалось отправить, в локальную очередь."""
    if not events:
        return
    conn = sqlite3.connect(PENDING_DB_FILE)
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO pending_events (process_name, window_title, started_at, ended_at, duration_seconds)
                VALUES (:process_name, :window_title, :started_at, :ended_at, :duration_seconds)
                """,
                events,
            )
    finally:
        conn.close()


def load_pending(limit: int | None = None) -> list[dict]:
    """
    Читает то, что накопилось в очереди — вместе с id (нужен для удаления
    после отправки). Всегда по возрастанию id (старые события первыми) —
    важно для отправки порциями в правильном порядке, не вперемешку.

    limit — необязательный параметр, НЕ значение по умолчанию, привязанное
    к константе (см. обсуждение ловушки: значения по умолчанию Python
    вычисляет один раз при определении функции, а не при каждом вызове —
    monkeypatch на константу тогда перестал бы работать в тестах).
    Без limit — всё как раньше, старое поведение не меняется.
    """
    conn = sqlite3.connect(PENDING_DB_FILE)
    try:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM pending_events ORDER BY id"
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def clear_pending(ids: list[int]) -> None:
    """Удаляет успешно отправленные события из очереди по их id."""
    if not ids:
        return
    conn = sqlite3.connect(PENDING_DB_FILE)
    try:
        with conn:
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM pending_events WHERE id IN ({placeholders})", ids)
    finally:
        conn.close()


def strip_id(events: list[dict]) -> list[dict]:
    """Убирает служебное поле id перед отправкой в API (бэкенд его не ждёт)."""
    return [{k: v for k, v in e.items() if k != "id"} for e in events]


# --- Блэклист системного мельканья ---

# window_title сравнивается точно. Если хочешь игнорировать процесс целиком
# независимо от заголовка — добавь (process_name, None).
IGNORE_LIST: set[tuple[str, str | None]] = {
    ("explorer.exe", "Переключение задач"),
    ("explorer.exe", ""),
}


def is_ignored(process_name: str, window_title: str) -> bool:
    """Проверяет, входит ли (process, title) в блэклист системных окон."""
    process_name = process_name.lower()
    for ignored_process, ignored_title in IGNORE_LIST:
        if process_name != ignored_process.lower():
            continue
        if ignored_title is None or ignored_title == window_title:
            return True
    return False


# --- Буфер закрытых интервалов, готовых к отправке (общий между потоками) ---

buffer: list[dict] = []
buffer_lock = threading.Lock()


def add_to_buffer(event: dict) -> None:
    """Добавляет готовый интервал в буфер отправки. Вызывается из колбэка хука."""
    with buffer_lock:
        buffer.append(event)


def flush_buffer() -> list[dict]:
    """Забирает всё, что накопилось в буфере, и очищает его. Вызывается фоновым потоком."""
    with buffer_lock:
        events = buffer.copy()
        buffer.clear()
    return events


# --- Аутентификация ---

_current_token: str | None = None


def login() -> str | None:
    """Логинится на бэкенде, сохраняет токен в памяти. Возвращает токен или None при неудаче."""
    global _current_token

    if not TRACKER_USERNAME or not TRACKER_PASSWORD:
        print("TRACKER_USERNAME/TRACKER_PASSWORD не заданы в .env — не могу залогиниться")
        return None

    try:
        response = requests.post(
            f"{API_URL}/auth/login",
            data={"username": TRACKER_USERNAME, "password": TRACKER_PASSWORD},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        _current_token = response.json()["access_token"]
        print("Успешный логин на бэкенде")
        return _current_token
    except requests.exceptions.RequestException as e:
        print(f"Не удалось залогиниться ({e.__class__.__name__})")
        return None


# --- Отправка на бэкенд ---

def send_batch(events: list[dict]) -> bool:
    """
    Пытается отправить пачку событий на бэкенд. Возвращает True при успехе.
    events — словари БЕЗ ключа "id" (это чисто SQLite-специфика, бэкенду не нужен).

    Логинится при первой необходимости (если токена ещё нет). Если сервер
    отклонит токен (401 — истёк или сброшен) — логинится заново и повторяет
    отправку один раз, прежде чем сдаться.
    """
    global _current_token

    if not events:
        return True

    if _current_token is None and login() is None:
        return False  # даже залогиниться не смогли — сеть точно недоступна

    def _post() -> requests.Response:
        headers = {"Authorization": f"Bearer {_current_token}"}
        return requests.post(
            f"{API_URL}/activities/batch",
            json={"events": events},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    try:
        response = _post()

        if response.status_code == 401:
            print("Токен не принят (401), логинюсь заново")
            if login() is None:
                return False
            response = _post()

        response.raise_for_status()
        print(f"Отправлено {len(events)} интервалов на сервер")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Не удалось отправить на сервер ({e.__class__.__name__}), сохраняю локально")
        return False


def flush_and_send() -> None:
    """
    Отправляет накопленное порциями по BATCH_LIMIT штук, пока очередь не
    опустеет или отправка не даст сбой.

    Буфер сразу сохраняется на диск (save_pending) — дальше вся функция
    работает с ОДНИМ источником (сама очередь), а не пытается по-разному
    обращаться с "тем, что из буфера" и "тем, что уже на диске". Это
    убирает целый класс возможных ошибок (перепутать, что откуда пришло),
    ценой одной лишней короткой записи на диск при каждом вызове.

    Останавливается на первой неудаче, не пытаясь пробить оставшиеся
    порции в этом же вызове — то, что не отправилось, и так уже лежит на
    диске (все события в очереди появляются там ДО попытки отправки),
    следующая попытка будет через SEND_INTERVAL_SECONDS, как обычно.
    """
    save_pending(flush_buffer())

    chunks_sent = 0
    while True:
        chunk = load_pending(limit=BATCH_LIMIT)
        if not chunk:
            break
        if not send_batch(strip_id(chunk)):
            break  # всё неотправленное уже на диске, ничего досохранять не нужно
        clear_pending([e["id"] for e in chunk])
        chunks_sent += 1

    if chunks_sent > 1:
        print(f"Очередь была большой: отправлено {chunks_sent} порций")