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


# --- Реестр коротких ключей для callback_data (issue #16) ---
#
# safe_callback_data() выше режет длинные значения под лимит Telegram —
# для КОРОТКИХ данных (process_name вроде "chrome.exe") это не проблема,
# но для названий сайтов (длинных, часто кириллических) обрезка МЕНЯЕТ
# значение, а не только длину: "...за 2026 год" -> "...за 2026". Это
# значение потом используется как ключ поиска (?site=...) в следующем
# запросе — обрезанное имя не совпадает ни с чем в БД.
#
# Решение: в callback_data кладём не само название сайта, а короткий
# числовой ключ; настоящее название хранится здесь, по chat_id — байтовый
# лимит Telegram физически недостижим для однозначной цифры.

_site_name_registry: dict[int, dict[str, str]] = {}


def reset_site_registry(chat_id: int) -> None:
    """
    Очищает реестр для чата. Вызывается перед отправкой НОВОГО набора
    кнопок с сайтами — иначе реестр рос бы бесконечно с каждым запросом
    статистики, а ключи из предыдущего набора кнопок (уже неактуальных)
    остались бы висеть в памяти.
    """
    _site_name_registry[chat_id] = {}


def register_site_name(chat_id: int, site_name: str) -> str:
    """
    Сохраняет полное название сайта под коротким ключом для этого чата,
    возвращает ключ. Ключи — просто порядковые номера ("0", "1", "2"...),
    этого достаточно: кнопок на одном экране максимум 10.
    """
    registry = _site_name_registry.setdefault(chat_id, {})
    key = str(len(registry))
    registry[key] = site_name
    return key


def resolve_site_name(chat_id: int, key: str) -> str | None:
    """
    Достаёт полное название сайта по короткому ключу. None, если реестр
    для чата не существует или ключ не найден (например, пользователь
    нажал на устаревшую кнопку из давно закрытого сообщения).
    """
    return _site_name_registry.get(chat_id, {}).get(key)