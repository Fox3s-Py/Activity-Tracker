import core


# --- is_ignored ---

def test_is_ignored_exact_match():
    assert core.is_ignored("explorer.exe", "Переключение задач") is True


def test_is_ignored_wildcard_empty_title():
    """("explorer.exe", "") в блэклисте — точное совпадение по пустому заголовку."""
    assert core.is_ignored("explorer.exe", "") is True


def test_is_ignored_case_insensitive_process_name():
    assert core.is_ignored("EXPLORER.EXE", "Переключение задач") is True


def test_is_ignored_title_mismatch():
    assert core.is_ignored("explorer.exe", "Какой-то другой заголовок") is False


def test_is_ignored_process_mismatch():
    assert core.is_ignored("chrome.exe", "Переключение задач") is False


# --- strip_id ---

def test_strip_id_removes_id_keeps_rest():
    events = [{"id": 1, "process_name": "chrome.exe", "duration_seconds": 5.0}]

    result = core.strip_id(events)

    assert result == [{"process_name": "chrome.exe", "duration_seconds": 5.0}]


def test_strip_id_multiple_events():
    events = [
        {"id": 1, "process_name": "chrome.exe"},
        {"id": 2, "process_name": "telegram.exe"},
    ]

    result = core.strip_id(events)

    assert result == [{"process_name": "chrome.exe"}, {"process_name": "telegram.exe"}]


def test_strip_id_event_without_id_untouched():
    """Клиентский буфер (не из очереди) не содержит id вообще — не должно падать."""
    events = [{"process_name": "chrome.exe"}]

    result = core.strip_id(events)

    assert result == [{"process_name": "chrome.exe"}]


# --- buffer ---

def test_add_and_flush_buffer():
    core.buffer.clear()  # изоляция от возможного состояния предыдущего теста

    core.add_to_buffer({"process_name": "chrome.exe"})
    core.add_to_buffer({"process_name": "telegram.exe"})

    result = core.flush_buffer()

    assert result == [{"process_name": "chrome.exe"}, {"process_name": "telegram.exe"}]


def test_flush_buffer_clears_it():
    core.buffer.clear()
    core.add_to_buffer({"process_name": "chrome.exe"})

    core.flush_buffer()
    second_flush = core.flush_buffer()

    assert second_flush == []


def test_flush_empty_buffer_returns_empty_list():
    core.buffer.clear()

    assert core.flush_buffer() == []


# --- SQLite-очередь (monkeypatch + tmp_path — не трогаем реальный pending_events.db) ---

def test_pending_queue_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "PENDING_DB_FILE", tmp_path / "test_pending.db")
    core.init_pending_db()

    events = [{
        "process_name": "chrome.exe", "window_title": "YouTube",
        "started_at": "2026-09-01T10:00:00", "ended_at": "2026-09-01T10:05:00",
        "duration_seconds": 300.0,
    }]
    core.save_pending(events)

    loaded = core.load_pending()

    assert len(loaded) == 1
    assert loaded[0]["process_name"] == "chrome.exe"
    assert "id" in loaded[0]  # id добавляется автоинкрементом, его не было при сохранении


def test_pending_queue_starts_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "PENDING_DB_FILE", tmp_path / "test_pending.db")
    core.init_pending_db()

    assert core.load_pending() == []


def test_save_pending_empty_list_does_nothing(monkeypatch, tmp_path):
    """Ранний return на пустом списке — не должно даже пытаться писать в БД."""
    monkeypatch.setattr(core, "PENDING_DB_FILE", tmp_path / "test_pending.db")
    core.init_pending_db()

    core.save_pending([])  # не должно упасть, БД пустая

    assert core.load_pending() == []


def test_clear_pending_removes_only_specified_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "PENDING_DB_FILE", tmp_path / "test_pending.db")
    core.init_pending_db()

    core.save_pending([
        {"process_name": "chrome.exe", "window_title": "A", "started_at": "2026-09-01T10:00:00",
         "ended_at": "2026-09-01T10:01:00", "duration_seconds": 60.0},
        {"process_name": "telegram.exe", "window_title": "B", "started_at": "2026-09-01T11:00:00",
         "ended_at": "2026-09-01T11:01:00", "duration_seconds": 60.0},
    ])

    all_events = core.load_pending()
    id_to_delete = all_events[0]["id"]
    id_to_keep = all_events[1]["id"]

    core.clear_pending([id_to_delete])

    remaining = core.load_pending()
    assert len(remaining) == 1
    assert remaining[0]["id"] == id_to_keep


def test_clear_pending_empty_list_does_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "PENDING_DB_FILE", tmp_path / "test_pending.db")
    core.init_pending_db()
    core.save_pending([{"process_name": "chrome.exe", "window_title": "A", "started_at": "2026-09-01T10:00:00",
                         "ended_at": "2026-09-01T10:01:00", "duration_seconds": 60.0}])

    core.clear_pending([])  # не должно ничего удалить

    assert len(core.load_pending()) == 1