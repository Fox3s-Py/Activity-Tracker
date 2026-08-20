"""
Activity Tracker — event-driven версия (Windows) + отправка на бэкенд.

Подписываемся на EVENT_SYSTEM_FOREGROUND / EVENT_OBJECT_NAMECHANGE через
WinAPI SetWinEventHook — Windows сама уведомляет о смене активного окна
и о смене заголовка (переключение вкладок в браузере).

Этап 3: интервалы больше не пишутся в SQLite напрямую. Вместо этого:
  1. Закрытые интервалы копятся в буфере в памяти (buffer)
  2. Отдельный фоновый поток раз в SEND_INTERVAL_SECONDS пробует отправить
     буфер батчем на бэкенд (POST /activities/batch)
  3. Если бэкенд недоступен — события сохраняются в локальную SQLite-очередь
     (pending_events.db) и досылаются при следующей успешной попытке

Требования:
    pip install psutil pywin32 requests

Запуск:
    python tracker_event.py
    (останавливается через Ctrl+C)
"""

import ctypes
import sqlite3
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import psutil
import requests
import win32gui

# --- Конфиг ---

API_URL = "http://127.0.0.1:8000/activities/batch"
SEND_INTERVAL_SECONDS = 300  # как часто пытаться отправить накопленное
REQUEST_TIMEOUT_SECONDS = 5

PENDING_DB_FILE = Path("pending_events.db")  # локальная очередь неотправленного

# Блэклист: (process_name, window_title) считаются "системным мельканием"
# и полностью игнорируются — как будто события не было, текущий интервал
# просто продолжается.
#
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


# --- WinAPI константы ---

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_NAMECHANGE = 0x800C
WINEVENT_OUTOFCONTEXT = 0x0000

OBJID_WINDOW = 0
CHILDID_SELF = 0

user32 = ctypes.windll.user32

WinEventProcType = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD,
)

# --- Состояние текущего открытого интервала (основной поток / колбэк хука) ---

current_window: tuple[str, str] | None = None
current_started_at: datetime | None = None
current_hwnd: int | None = None

# --- Буфер закрытых интервалов, готовых к отправке (общий между потоками) ---

buffer: list[dict] = []
buffer_lock = threading.Lock()


# --- Буфер в памяти ---

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


# --- Локальная SQLite-очередь неотправленного ---

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


# --- Отправка на бэкенд ---

def send_batch(events: list[dict]) -> bool:
    """
    Пытается отправить пачку событий на бэкенд. Возвращает True при успехе.
    events — словари БЕЗ ключа "id" (это чисто SQLite-специфика, бэкенду не нужен).
    """
    if not events:
        return True

    try:
        response = requests.post(API_URL, json={"events": events}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        print(f"Отправлено {len(events)} интервалов на сервер")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Не удалось отправить на сервер ({e.__class__.__name__}), сохраняю локально")
        return False


def strip_id(events: list[dict]) -> list[dict]:
    """Убирает служебное поле id перед отправкой в API (бэкенд его не ждёт)."""
    return [{k: v for k, v in e.items() if k != "id"} for e in events]


def flush_and_send() -> None:
    """
    Полный цикл одной попытки отправки:
    забрать буфер + то, что скопилось в очереди -> отправить -> почистить успешное.
    """
    current_events = flush_buffer()
    pending_events = load_pending()

    all_events = strip_id(pending_events) + current_events

    if not all_events:
        return

    if send_batch(all_events):
        # успех — очищаем и буфер (уже забрали выше), и старую очередь целиком
        pending_ids = [e["id"] for e in pending_events]
        clear_pending(pending_ids)
    else:
        # не получилось — сохраняем то, что было в буфере, в очередь
        # (то, что уже лежало в pending_events, там и остаётся, не трогаем)
        save_pending(current_events)


def send_periodically() -> None:
    """Фоновый поток: раз в SEND_INTERVAL_SECONDS пытается отправить накопленное."""
    while True:
        time.sleep(SEND_INTERVAL_SECONDS)
        flush_and_send()


# --- Работа с окнами (WinAPI) ---

def get_window_info(hwnd: int) -> tuple[str, str] | None:
    """По хэндлу окна возвращает (process_name, window_title)."""
    if not hwnd:
        return None

    window_title = win32gui.GetWindowText(hwnd)

    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = psutil.Process(pid.value)
        process_name = process.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        process_name = "unknown"

    if not window_title and process_name == "unknown":
        return None

    return process_name, window_title


def close_interval(process_name: str, window_title: str, started_at: datetime, ended_at: datetime) -> None:
    """Закрывает интервал: кладёт его в буфер отправки вместо прямой записи в БД."""
    duration = (ended_at - started_at).total_seconds()
    add_to_buffer({
        "process_name": process_name,
        "window_title": window_title,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round(duration, 1),
    })
    print(f"[{ended_at:%H:%M:%S}] {process_name} — {window_title!r} ({duration:.1f}s)")


def on_window_event(hWinEventHook, event, hwnd, idObject, idChild, idEventThread, dwmsEventTime):
    """
    Общий колбэк на два типа событий:
      - EVENT_SYSTEM_FOREGROUND — сменилось активное окно
      - EVENT_OBJECT_NAMECHANGE — сменился заголовок текущего активного окна
        (например, переключение вкладки в браузере — hwnd тот же, меняется только title)
    """
    global current_window, current_started_at, current_hwnd

    if event == EVENT_SYSTEM_FOREGROUND:
        current_hwnd = hwnd
    elif event == EVENT_OBJECT_NAMECHANGE:
        if idObject != OBJID_WINDOW or idChild != CHILDID_SELF:
            return
        if hwnd != current_hwnd:
            return
    else:
        return

    window = get_window_info(hwnd)
    now = datetime.now()

    if window is None:
        return

    process_name, window_title = window

    if is_ignored(process_name, window_title):
        return

    if window == current_window:
        return

    if current_window is not None and current_started_at is not None:
        prev_process, prev_title = current_window
        close_interval(prev_process, prev_title, current_started_at, now)

    current_window = window
    current_started_at = now


def run() -> None:
    global current_window, current_started_at, current_hwnd

    init_pending_db()

    current_hwnd = win32gui.GetForegroundWindow()
    current_window = get_window_info(current_hwnd)
    current_started_at = datetime.now()

    # фоновый поток периодической отправки — не блокирует основной цикл хука
    sender_thread = threading.Thread(target=send_periodically, daemon=True)
    sender_thread.start()

    callback = WinEventProcType(on_window_event)

    hook_foreground = user32.SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, 0, callback, 0, 0, WINEVENT_OUTOFCONTEXT,
    )
    hook_namechange = user32.SetWinEventHook(
        EVENT_OBJECT_NAMECHANGE, EVENT_OBJECT_NAMECHANGE, 0, callback, 0, 0, WINEVENT_OUTOFCONTEXT,
    )

    if not hook_foreground or not hook_namechange:
        raise RuntimeError("Не удалось установить SetWinEventHook")

    print(f"Трекер запущен. Отправка на {API_URL} каждые {SEND_INTERVAL_SECONDS} сек.")
    print(f"Локальная очередь (на случай недоступности сервера): {PENDING_DB_FILE.resolve()}")
    print("Останови через Ctrl+C.")

    try:
        win32gui.PumpMessages()
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnhookWinEvent(hook_foreground)
        user32.UnhookWinEvent(hook_namechange)

        if current_window is not None and current_started_at is not None:
            process_name, window_title = current_window
            close_interval(process_name, window_title, current_started_at, datetime.now())

        # финальная попытка отправить всё, что скопилось, перед выходом
        flush_and_send()

        print("\nОстановлено.")


if __name__ == "__main__":
    run()