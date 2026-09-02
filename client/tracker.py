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
    python tracker.py
    (останавливается командой 'stop' в консоли, НЕ через Ctrl+C — см. ignore_sigint)
"""

import ctypes
import os
import signal
import threading
import time
from ctypes import wintypes
from datetime import datetime

import psutil
import win32gui
from dotenv import load_dotenv

from core import (
    PENDING_DB_FILE,
    API_URL,
    is_ignored,
    strip_id,
    add_to_buffer,
    flush_buffer,
    init_pending_db,
    save_pending,
    load_pending,
    clear_pending,
    login,
    send_batch,
    flush_and_send,
)

load_dotenv()

# --- Конфиг ---

SEND_INTERVAL_SECONDS = 300  # как часто пытаться отправить накопленное

# --- WinAPI константы ---

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_NAMECHANGE = 0x800C
WINEVENT_OUTOFCONTEXT = 0x0000

OBJID_WINDOW = 0
CHILDID_SELF = 0

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

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
    window_title = window_title.replace("\x00", "")  # защита от NUL-байтов из WinAPI

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


# --- Штатная остановка (без KeyboardInterrupt/Ctrl+C) ---

WM_QUIT = 0x0012


def stdin_listener(main_thread_id: int) -> None:
    """
    Слушает консоль в отдельном потоке. По команде 'stop' посылает WM_QUIT
    в основной поток — это штатный способ выйти из PumpMessages(), без
    KeyboardInterrupt и без мусора в консоли.
    """
    while True:
        try:
            command = input().strip().lower()
        except EOFError:
            break

        if command == "stop":
            print("Получена команда остановки, завершаюсь...")
            user32.PostThreadMessageW(main_thread_id, WM_QUIT, 0, 0)
            break


def ignore_sigint(signum, frame) -> None:
    """Ctrl+C больше не завершает трекер — только мешает 'грязным' способом. Используй 'stop'."""
    print("\nCtrl+C отключён. Чтобы остановить трекер, набери 'stop' и нажми Enter.")


signal.signal(signal.SIGINT, ignore_sigint)


def run() -> None:
    global current_window, current_started_at, current_hwnd

    init_pending_db()

    current_hwnd = win32gui.GetForegroundWindow()
    current_window = get_window_info(current_hwnd)
    current_started_at = datetime.now()

    # фоновый поток периодической отправки — не блокирует основной цикл хука
    sender_thread = threading.Thread(target=send_periodically, daemon=True)
    sender_thread.start()

    # запоминаем id ОСНОВНОГО потока — именно в него нужно послать WM_QUIT
    main_thread_id = kernel32.GetCurrentThreadId()
    listener_thread = threading.Thread(target=stdin_listener, args=(main_thread_id,), daemon=True)
    listener_thread.start()

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
    print("Чтобы остановить — набери 'stop' и нажми Enter.")

    win32gui.PumpMessages()  # завершится штатно, как только придёт WM_QUIT от stdin_listener

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