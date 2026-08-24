from app.main import extract_site


def test_extract_site_removes_telegram_unread_counter():
    cases = [
        # (что подали на вход, что должно получиться)
        ("Андрей - (23232)", "Андрей"),
        ("Милостивая Государыня – (35803)", "Милостивая Государыня"),  # длинное тире
        ("(3) Андрей - (23232)", "Андрей"),  # ведущий счётчик
        ("\u200eВыжившие работяги – (35804)", "Выжившие работяги"),  # невидимый символ
    ]

    for raw_title, expected in cases:
        assert extract_site(raw_title) == expected


def test_extract_site_extracts_browser_suffix():
    cases = [
        ("UL - Cash Lobby - Google Chrome", "Google Chrome"),
        ("Activity Tracker - GitHub", "GitHub"),
    ]

    for raw_title, expected in cases:
        assert extract_site(raw_title) == expected


def test_extract_site_handles_empty_and_none():
    assert extract_site("") == "Без названия"
    assert extract_site(None) == "Без названия"  # type: ignore