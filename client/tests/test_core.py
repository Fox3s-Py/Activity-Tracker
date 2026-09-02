import core
import requests

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


# --- is_ignored: wildcard-случай (ignored_title=None), которого нет в примерах выше ---

def test_is_ignored_wildcard_matches_any_title(monkeypatch):
    """
    (process_name, None) в IGNORE_LIST означает "игнорировать процесс целиком,
    независимо от заголовка". В реальном IGNORE_LIST такой записи сейчас нет
    (только конкретные заголовки) — эта ветка кода не выполнялась ни разу
    ни одним существующим тестом до этого.
    """
    monkeypatch.setattr(core, "IGNORE_LIST", {("systemprocess.exe", None)})

    assert core.is_ignored("systemprocess.exe", "Любой заголовок") is True
    assert core.is_ignored("systemprocess.exe", "") is True
    assert core.is_ignored("systemprocess.exe", "Совсем другой") is True


# --- buffer: многопоточность (ради чего вообще существует buffer_lock) ---

def test_buffer_survives_concurrent_add_and_flush():
    """
    add_to_buffer() и flush_buffer() дёргаются из РАЗНЫХ потоков в реальном
    приложении (колбэк хука пишет, фоновый поток отправки читает). Без
    buffer_lock есть окно гонки: поток A скопировал buffer, поток B успел
    добавить туда событие, поток A очистил buffer — событие B потеряно.

    ВАЖНО: тест на многопоточность вероятностный, не детерминированный —
    проверено на практике: без buffer_lock тест падает примерно в 3 запусках
    из 8, а не гарантированно каждый раз (GIL сериализует байткод-операции,
    поэтому окно гонки не всегда успевает открыться). С buffer_lock на месте
    тест стабильно зелёный (10/10 прогонов). Это ожидаемая характеристика
    тестов конкурентного доступа, а не брак самого теста.
    """
    import threading

    core.buffer.clear()
    collected: list[dict] = []
    collected_lock = threading.Lock()

    EVENTS_PER_PRODUCER = 500
    PRODUCERS = 8

    def produce(producer_id: int):
        for i in range(EVENTS_PER_PRODUCER):
            core.add_to_buffer({"producer": producer_id, "i": i})

    def collect_periodically(stop_event: threading.Event):
        while not stop_event.is_set():
            flushed = core.flush_buffer()
            with collected_lock:
                collected.extend(flushed)

    stop_event = threading.Event()
    collector = threading.Thread(target=collect_periodically, args=(stop_event,))
    collector.start()

    producers = [threading.Thread(target=produce, args=(pid,)) for pid in range(PRODUCERS)]
    for t in producers:
        t.start()
    for t in producers:
        t.join()

    stop_event.set()
    collector.join()

    # финальный flush — забрать всё, что могло остаться после остановки коллектора
    collected.extend(core.flush_buffer())

    assert len(collected) == PRODUCERS * EVENTS_PER_PRODUCER

# --- login / send_batch (сеть подделана через monkeypatch на requests.post) ---

def test_login_without_credentials_returns_none_without_network_call(monkeypatch):
    monkeypatch.setattr(core, "TRACKER_USERNAME", None)
    monkeypatch.setattr(core, "TRACKER_PASSWORD", None)

    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: calls.append(1))

    result = core.login()

    assert result is None
    assert calls == []

class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_data


def test_login_success_stores_token(monkeypatch):
    monkeypatch.setattr(core, "TRACKER_USERNAME", "testuser")
    monkeypatch.setattr(core, "TRACKER_PASSWORD", "testpass")
    monkeypatch.setattr(core, "_current_token", None)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: FakeResponse(200, {"access_token": "abc123"}))

    result = core.login()

    assert result == "abc123"
    assert core._current_token == "abc123"

def test_send_batch_empty_events_returns_true_without_network_call(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: calls.append(1))

    assert core.send_batch([]) is True
    assert calls == []

def test_send_batch_retries_after_401(monkeypatch):
    monkeypatch.setattr(core, "TRACKER_USERNAME", "testuser")
    monkeypatch.setattr(core, "TRACKER_PASSWORD", "testpass")
    monkeypatch.setattr(core, "_current_token", "stale-token")

    call_log = []

    def fake_post(url, **kwargs):
        if "/auth/login" in url:
            call_log.append("login")
            return FakeResponse(200, {"access_token": "fresh-token"})
        call_log.append("batch")
        # первая попытка батча — 401 (протухший токен), вторая — успех
        if call_log.count("batch") == 1:
            return FakeResponse(401)
        return FakeResponse(200)

    monkeypatch.setattr(requests, "post", fake_post)

    result = core.send_batch([{"process_name": "chrome.exe"}])

    assert result is True
    assert call_log == ["batch", "login", "batch"]