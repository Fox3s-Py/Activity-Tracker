"""
bot/formatting.py — чистая логика бота: диапазоны дат, форматирование
длительности, сборка callback_data.

Намеренно БЕЗ импорта aiogram.Bot/Dispatcher — модуль не создаёт объект
Bot и не требует TELEGRAM_BOT_TOKEN на уровне импорта. Именно поэтому его
можно импортировать (и тестировать) где угодно, включая CI, без единой
настоящей переменной окружения бота.
"""

from datetime import date, timedelta

# Код периода, который "путешествует" вместе с кнопками через все уровни
# drill-down (callback_data) — чтобы детализация внутри Chrome/Telegram
# считалась за тот же диапазон дат, что и верхнеуровневая статистика,
# а не всегда "за сегодня" по умолчанию.
PERIOD_TODAY = "t"
PERIOD_YESTERDAY = "y"
PERIOD_WEEK = "w"

PERIOD_LABELS = {
    PERIOD_TODAY: "сегодня",
    PERIOD_YESTERDAY: "вчера",
    PERIOD_WEEK: "за неделю",
}


def period_date_range(period_code: str) -> tuple[date, date]:
    """По короткому коду периода — диапазон дат для запроса к API."""
    today = date.today()
    if period_code == PERIOD_YESTERDAY:
        d = today - timedelta(days=1)
        return d, d
    if period_code == PERIOD_WEEK:
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        return week_start, week_end
    return today, today  # по умолчанию — сегодня


def format_duration(total_seconds: float) -> str:
    """Секунды -> 'Xч Yм Zс'."""
    total = int(total_seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours}ч {minutes}м {seconds}с"


def safe_callback_data(prefix: str, *parts: str, max_length: int = 64) -> str:
    """
    Собирает callback_data из частей через ':' и обрезает при необходимости,
    чтобы влезть в реальный лимит Telegram (64 байта). Обрезается последняя
    часть, по одному символу за раз — чтобы не срезать больше нужного.
    """
    data = ":".join([prefix, *parts])
    if len(data.encode("utf-8")) <= max_length:
        return data

    *head, last = parts
    while last and len(data.encode("utf-8")) > max_length:
        last = last[:-1]
        data = ":".join([prefix, *head, last])
    return data