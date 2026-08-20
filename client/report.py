"""
Activity Tracker — консольный отчёт (тестовый, этап 2.5).

Отдельный скрипт, читает ту же activity.db, что пишет tracker_event.py.
Запускается параллельно с трекером в другом терминале.

Команды:
    сводка   — топ-10 окон по суммарному времени
    выход    — закрыть

Запуск:
    python report.py
"""

import sqlite3
from pathlib import Path

DB_FILE = Path("activity.db")


def format_duration(seconds: float) -> str:
    """Секунды -> человекочитаемая строка вида 1h 23m 04s."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def print_top_n(n: int = 10) -> None:
    if not DB_FILE.exists():
        print(f"База {DB_FILE.resolve()} не найдена. Запусти tracker_event.py, пусть накопит данные.")
        return

    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            """
            SELECT process_name, window_title, SUM(duration_seconds) AS total_seconds
            FROM activity_events
            GROUP BY process_name, window_title
            ORDER BY total_seconds DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()

    if not rows:
        print("Пока нет данных.")
        return

    print(f"\nТоп-{n} по времени:\n")
    for i, (process_name, window_title, total_seconds) in enumerate(rows, start=1):
        title = window_title or "(без заголовка)"
        print(f"{i:>2}. {format_duration(total_seconds):>10}  {process_name:<20} {title}")
    print()


def run() -> None:
    print("Activity Tracker — консольный отчёт.")
    print("Команды: 'сводка' — топ-10, 'выход' — закрыть.\n")

    while True:
        try:
            command = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if command == "сводка":
            print_top_n(10)
        elif command in ("выход", "exit", "quit"):
            break
        elif command:
            print("Не знаю такую команду. Доступно: 'сводка', 'выход'.")


if __name__ == "__main__":
    run()