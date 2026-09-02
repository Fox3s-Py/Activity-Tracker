from datetime import date, timedelta

from formatting import (
    PERIOD_TODAY,
    PERIOD_YESTERDAY,
    PERIOD_WEEK,
    format_duration,
    period_date_range,
    safe_callback_data,
)


# --- format_duration ---

def test_format_duration_zero():
    assert format_duration(0) == "0ч 0м 0с"


def test_format_duration_exact_hour():
    assert format_duration(3600) == "1ч 0м 0с"


def test_format_duration_rounds_down_fraction():
    """59.9 секунд -> 59с, не округляется вверх до 1м0с (int() всегда режет вниз)."""
    assert format_duration(59.9) == "0ч 0м 59с"


def test_format_duration_combines_all_units():
    # 2ч 5м 10с = 2*3600 + 5*60 + 10
    assert format_duration(2 * 3600 + 5 * 60 + 10) == "2ч 5м 10с"


def test_format_duration_negative():
    """
    Характеризующий тест: фиксирует ТЕКУЩЕЕ поведение на отрицательном
    значении, а не то, что оно обязательно правильное. duration_seconds
    отрицательным быть не должно (валидация на backend, issue #4) — но если
    оно всё же сюда попадёт, int()/// для отрицательных чисел в Python
    округляет к минус бесконечности, а не к нулю, отсюда контринтуитивный
    результат. Задокументировано, не исправлено в рамках этого issue.
    """
    assert format_duration(-5) == "-1ч 59м 55с"


# --- period_date_range ---

def test_period_date_range_today_default():
    result = period_date_range("unknown-code")
    assert result == (date.today(), date.today())


def test_period_date_range_today_explicit():
    assert period_date_range(PERIOD_TODAY) == (date.today(), date.today())


def test_period_date_range_yesterday():
    yesterday = date.today() - timedelta(days=1)
    assert period_date_range(PERIOD_YESTERDAY) == (yesterday, yesterday)


def test_period_date_range_week_boundaries():
    """Понедельник-воскресенье текущей недели."""
    today = date.today()
    expected_start = today - timedelta(days=today.weekday())
    expected_end = expected_start + timedelta(days=6)

    week_start, week_end = period_date_range(PERIOD_WEEK)

    assert week_start == expected_start
    assert week_end == expected_end
    assert week_start.weekday() == 0  # понедельник
    assert week_end.weekday() == 6    # воскресенье


# --- safe_callback_data ---

def test_safe_callback_data_short_data_unchanged():
    result = safe_callback_data("breakdown", "t", "chrome.exe")
    assert result == "breakdown:t:chrome.exe"


def test_safe_callback_data_result_fits_64_bytes():
    result = safe_callback_data("title", "t", "Telegram.exe", "Выжившие работяги за 2026 год")
    assert len(result.encode("utf-8")) <= 64


def test_safe_callback_data_truncation_produces_valid_utf8():
    """Обрезка идёт по символам (parts[-1][:-1]), не по сырым байтам —
    не может разрезать многобайтовый символ пополам."""
    result = safe_callback_data("title", "t", "Telegram.exe", "Выжившие работяги за 2026 год")
    result.encode("utf-8").decode("utf-8")  # не должно поднять UnicodeError


def test_safe_callback_data_truncation_changes_value():
    """
    Характеризующий тест на баг из issue #16: обрезка меняет ЗНАЧЕНИЕ
    (не только длину), а это значение потом используется как ключ поиска
    в БД (?site=...) — обрезанное имя не совпадает ни с чем. Фиксирует
    текущее (проблемное) поведение перед архитектурным фиксом.
    """
    original_site_name = "Выжившие работяги за 2026 год"
    result = safe_callback_data("title", "t", "Telegram.exe", original_site_name)

    truncated_site_name = result.split(":", 3)[3]
    assert truncated_site_name != original_site_name  # ЗНАЧЕНИЕ изменилось — источник бага