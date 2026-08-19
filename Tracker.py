"""
Activity Tracker — event-driven версия (Windows) + локальная SQLite.

Подписываемся на EVENT_SYSTEM_FOREGROUND через WinAPI SetWinEventHook —
Windows сама уведомляет нас в момент смены активного окна.

Этап 2: вместо CSV пишем интервалы в локальную SQLite-базу (activity.db).
Плюс блэклист системных окон-мельканий (Alt+Tab / "Переключение задач"
и т.п.), чтобы они не засоряли статистику ложными микро-интервалами.

Требования:
    pip install psutil pywin32

Запуск:
    python tracker_event.py
    (останавливается через Ctrl+C)
"""

import ctypes
import sqlite3
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import psutil
import win32gui

# --- Конфиг ---

DB_FILE = Path("activity.db")

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
EVENT_OBJECT_NAMECHANGE = 0x800C   # смена заголовка объекта (в т.ч. переключение вкладок в браузере)
WINEVENT_OUTOFCONTEXT = 0x0000

OBJID_WINDOW = 0        # событие относится к самому окну, а не к его дочерним контролам
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

# --- Состояние текущего открытого интервала ---

current_window: tuple[str, str] | None = None
current_started_at: datetime | None = None
current_hwnd: int | None = None   # хэндл активного окна — нужен, чтобы фильтровать NAMECHANGE
                                    # только по нему, а не по фоновым окнам


def init_db() -> None:
    """Создаёт таблицу activity_events, если её ещё нет."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                process_name TEXT NOT NULL,
                window_title TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL
            )
            """
        )
        # индекс пригодится на этапе агрегации/отчётов
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_started_at ON activity_events (started_at)"
        )
        conn.commit()


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


def append_event(started_at: datetime, ended_at: datetime, process_name: str, window_title: str) -> None:
    """Записывает один готовый интервал в SQLite."""
    duration = (ended_at - started_at).total_seconds()

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO activity_events (process_name, window_title, started_at, ended_at, duration_seconds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (process_name, window_title, started_at.isoformat(), ended_at.isoformat(), round(duration, 1)),
        )
        conn.commit()


def on_window_event(hWinEventHook, event, hwnd, idObject, idChild, idEventThread, dwmsEventTime):
    """
    Общий колбэк на два типа событий:
      - EVENT_SYSTEM_FOREGROUND — сменилось активное окно (переключение между приложениями)
      - EVENT_OBJECT_NAMECHANGE — сменился заголовок текущего активного окна
        (например, переключение вкладки в браузере — hwnd тот же, меняется только title)
    В обоих случаях закрывает старый интервал и открывает новый.
    """
    global current_window, current_started_at, current_hwnd

    if event == EVENT_SYSTEM_FOREGROUND:
        current_hwnd = hwnd
    elif event == EVENT_OBJECT_NAMECHANGE:
        # NAMECHANGE стреляет для кучи разных объектов (не только окон) —
        # нас интересует только сам объект окна, и только если это текущее активное окно
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

    # Системное мелькание (Alt+Tab и т.п.) — полностью игнорируем событие,
    # как будто активное окно не менялось.
    if is_ignored(process_name, window_title):
        return

    if window == current_window:
        return

    if current_window is not None and current_started_at is not None:
        prev_process, prev_title = current_window
        append_event(current_started_at, now, prev_process, prev_title)
        print(f"[{now:%H:%M:%S}] {prev_process} — {prev_title!r} "
              f"({(now - current_started_at).total_seconds():.1f}s)")

    current_window = window
    current_started_at = now


def run() -> None:
    global current_window, current_started_at, current_hwnd

    init_db()

    current_hwnd = win32gui.GetForegroundWindow()
    current_window = get_window_info(current_hwnd)
    current_started_at = datetime.now()

    callback = WinEventProcType(on_window_event)

    # Хук №1: смена активного окна (переключение между приложениями)
    hook_foreground = user32.SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_FOREGROUND,
        0,
        callback,
        0,
        0,
        WINEVENT_OUTOFCONTEXT,
    )

    # Хук №2: смена заголовка текущего окна (переключение вкладок в браузере и т.п.)
    hook_namechange = user32.SetWinEventHook(
        EVENT_OBJECT_NAMECHANGE,
        EVENT_OBJECT_NAMECHANGE,
        0,
        callback,
        0,
        0,
        WINEVENT_OUTOFCONTEXT,
    )

    if not hook_foreground or not hook_namechange:
        raise RuntimeError("Не удалось установить SetWinEventHook")

    print(f"Трекер запущен (event-driven, с трекингом вкладок). База: {DB_FILE.resolve()}")
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
            append_event(current_started_at, datetime.now(), process_name, window_title)
        print("\nОстановлено. Данные сохранены в", DB_FILE.resolve())


if __name__ == "__main__":
    run()