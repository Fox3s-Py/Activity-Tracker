"""
client/core.py — чистая логика клиента: фильтрация системных окон, буфер
в памяти, локальная SQLite-очередь неотправленного.

Намеренно БЕЗ ctypes/win32gui/psutil — модуль не трогает WinAPI ни в одной
строке, поэтому импортируется и тестируется где угодно, включая CI на
Linux, где WinAPI физически не существует.
"""

import sqlite3
import threading
from pathlib import Path

# --- Локальная SQLite-очередь неотправленного ---

PENDING_DB_FILE = Path("pending_events.db")


def init_pending_db() -> None:
    """Таблица-очередь: сюда падают события, которые не удалось отправить."""
    with sqlite3.connect(PENDING_DB_FILE) as conn:
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
        conn.commit()


def save_pending(events: list[dict]) -> None:
    """Сохраняет события, которые не удалось отправить, в локальную очередь."""
    if not events:
        return
    with sqlite3.connect(PENDING_DB_FILE) as conn:
        conn.executemany(
            """
            INSERT INTO pending_events (process_name, window_title, started_at, ended_at, duration_seconds)
            VALUES (:process_name, :window_title, :started_at, :ended_at, :duration_seconds)
            """,
            events,
        )
        conn.commit()


def load_pending() -> list[dict]:
    """Читает всё, что накопилось в очереди — вместе с id (нужен для удаления после отправки)."""
    with sqlite3.connect(PENDING_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM pending_events").fetchall()
        return [dict(row) for row in rows]


def clear_pending(ids: list[int]) -> None:
    """Удаляет успешно отправленные события из очереди по их id."""
    if not ids:
        return
    with sqlite3.connect(PENDING_DB_FILE) as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM pending_events WHERE id IN ({placeholders})", ids)
        conn.commit()


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