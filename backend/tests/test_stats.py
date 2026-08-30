def test_daily_stats_aggregates_by_process(client, auth_headers):
    # Сначала кладём данные в базу — через тот же /activities/batch,
    # которым уже пользовались в прошлых тестах
    client.post("/activities/batch", headers=auth_headers, json={
        "events": [
            {
                "process_name": "chrome.exe",
                "window_title": "A",
                "started_at": "2026-08-21T10:00:00",
                "ended_at": "2026-08-21T10:05:00",
                "duration_seconds": 300.0,
            },
            {
                "process_name": "chrome.exe",
                "window_title": "B",
                "started_at": "2026-08-21T11:00:00",
                "ended_at": "2026-08-21T11:10:00",
                "duration_seconds": 600.0,
            },
            {
                "process_name": "Code.exe",
                "window_title": "C",
                "started_at": "2026-08-21T12:00:00",
                "ended_at": "2026-08-21T12:20:00",
                "duration_seconds": 1200.0,
            },
        ]
    })

    # Теперь запрашиваем агрегацию за тот же день
    response = client.get("/stats/daily", params={"target_date": "2026-08-21"}, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()

    # chrome.exe должен схлопнуться в одну строку: 300 + 600 = 900
    stats_by_process = {item["process_name"]: item["total_seconds"] for item in data["stats"]}
    assert stats_by_process["chrome.exe"] == 900.0
    assert stats_by_process["Code.exe"] == 1200.0

    # Code.exe (1200 сек) должен быть выше chrome.exe (900 сек) — сортировка по убыванию
    assert data["stats"][0]["process_name"] == "Code.exe"


def test_weekly_stats_aggregates_and_respects_week_boundaries(client, auth_headers):
    # Неделя 2026-08-17 (пн) - 2026-08-23 (вс). Кладём события внутри
    # недели в разные дни + одно событие ЗА пределами недели.
    client.post("/activities/batch", headers=auth_headers, json={
        "events": [
            {
                "process_name": "chrome.exe",
                "window_title": "A",
                "started_at": "2026-08-17T10:00:00",  # понедельник (начало недели)
                "ended_at": "2026-08-17T10:05:00",
                "duration_seconds": 300.0,
            },
            {
                "process_name": "chrome.exe",
                "window_title": "B",
                "started_at": "2026-08-20T11:00:00",  # четверг (та же неделя)
                "ended_at": "2026-08-20T11:10:00",
                "duration_seconds": 600.0,
            },
            {
                "process_name": "chrome.exe",
                "window_title": "C",
                "started_at": "2026-08-24T09:00:00",  # ПОНЕДЕЛЬНИК СЛЕДУЮЩЕЙ недели
                "ended_at": "2026-08-24T09:01:00",
                "duration_seconds": 60.0,
            },
        ]
    })

    # Запрашиваем неделю, указав любую дату внутри неё (четверг)
    response = client.get("/stats/weekly", params={"target_date": "2026-08-20"}, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()

    assert data["week_start"] == "2026-08-17"
    assert data["week_end"] == "2026-08-23"

    stats_by_process = {item["process_name"]: item["total_seconds"] for item in data["stats"]}

    # Только два события ВНУТРИ недели: 300 + 600 = 900.
    # Событие из следующей недели (60 сек) НЕ должно попасть в сумму.
    assert stats_by_process["chrome.exe"] == 900.0


def test_daily_stats_shows_only_own_data(client):
    """
    Регрессия на саму суть мультипользовательского режима: Алиса не должна
    видеть статистику Боба и наоборот, даже если оба трекали в один и тот
    же день.
    """
    client.post("/auth/register", json={"username": "alice", "password": "pass123456"})
    client.post("/auth/register", json={"username": "bob", "password": "pass123456"})

    alice_token = client.post("/auth/login", data={"username": "alice", "password": "pass123456"}).json()["access_token"]
    bob_token = client.post("/auth/login", data={"username": "bob", "password": "pass123456"}).json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    # Алиса трекала chrome.exe, Боб — Code.exe, в один и тот же день
    client.post("/activities/batch", headers=alice_headers, json={"events": [{
        "process_name": "chrome.exe", "window_title": "Alice's browser",
        "started_at": "2026-08-25T10:00:00", "ended_at": "2026-08-25T10:05:00", "duration_seconds": 300.0,
    }]})
    client.post("/activities/batch", headers=bob_headers, json={"events": [{
        "process_name": "Code.exe", "window_title": "Bob's editor",
        "started_at": "2026-08-25T11:00:00", "ended_at": "2026-08-25T11:10:00", "duration_seconds": 600.0,
    }]})

    alice_stats = client.get("/stats/daily", params={"target_date": "2026-08-25"}, headers=alice_headers).json()
    bob_stats = client.get("/stats/daily", params={"target_date": "2026-08-25"}, headers=bob_headers).json()

    alice_processes = {item["process_name"] for item in alice_stats["stats"]}
    bob_processes = {item["process_name"] for item in bob_stats["stats"]}

    assert alice_processes == {"chrome.exe"}
    assert bob_processes == {"Code.exe"}

    # железобетонная проверка: чужого процесса в списке быть не должно вообще
    assert "Code.exe" not in alice_processes
    assert "chrome.exe" not in bob_processes

def test_daily_stats_requires_auth(client):
    response = client.get("/stats/daily")  # без headers=auth_headers

    assert response.status_code == 401


def test_weekly_stats_requires_auth(client):
    response = client.get("/stats/weekly")

    assert response.status_code == 401


def test_breakdown_stats_requires_auth(client):
    response = client.get("/stats/daily/breakdown", params={"process_name": "chrome.exe"})

    assert response.status_code == 401


def test_breakdown_groups_by_site(client, auth_headers):
    """
    Уровень 2 (без ?site=): несколько разных window_title, extract_site
    сводит два первых к одному ключу "Google Chrome" (проверено в
    test_extract_site.py), третий остаётся отдельно как "GitHub".
    """
    client.post("/activities/batch", headers=auth_headers, json={"events": [
        {
            "process_name": "chrome.exe", "window_title": "UL - Cash Lobby - Google Chrome",
            "started_at": "2026-08-25T10:00:00", "ended_at": "2026-08-25T10:05:00", "duration_seconds": 300.0,
        },
        {
            "process_name": "chrome.exe", "window_title": "Другая страница - Google Chrome",
            "started_at": "2026-08-25T11:00:00", "ended_at": "2026-08-25T11:03:20", "duration_seconds": 200.0,
        },
        {
            "process_name": "chrome.exe", "window_title": "Activity Tracker - GitHub",
            "started_at": "2026-08-25T12:00:00", "ended_at": "2026-08-25T12:01:40", "duration_seconds": 100.0,
        },
    ]})

    response = client.get("/stats/daily/breakdown", params={
        "process_name": "chrome.exe", "date_from": "2026-08-25", "date_to": "2026-08-25",
    }, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["level"] == "site"

    totals = {item["name"]: item["total_seconds"] for item in data["breakdown"]}
    assert totals["Google Chrome"] == 500.0  # 300 + 200, схлопнулись в одну строку
    assert totals["GitHub"] == 100.0


def test_breakdown_shows_titles_within_site(client, auth_headers):
    """Уровень 3 (с ?site=Google Chrome): те же два chrome-события — теперь
    НЕ схлопываются, каждый window_title отдельной строкой; GitHub исключён
    полностью, потому что не относится к выбранному сайту."""
    client.post("/activities/batch", headers=auth_headers, json={"events": [
        {
            "process_name": "chrome.exe", "window_title": "UL - Cash Lobby - Google Chrome",
            "started_at": "2026-08-25T10:00:00", "ended_at": "2026-08-25T10:05:00", "duration_seconds": 300.0,
        },
        {
            "process_name": "chrome.exe", "window_title": "Другая страница - Google Chrome",
            "started_at": "2026-08-25T11:00:00", "ended_at": "2026-08-25T11:03:20", "duration_seconds": 200.0,
        },
        {
            "process_name": "chrome.exe", "window_title": "Activity Tracker - GitHub",
            "started_at": "2026-08-25T12:00:00", "ended_at": "2026-08-25T12:01:40", "duration_seconds": 100.0,
        },
    ]})

    response = client.get("/stats/daily/breakdown", params={
        "process_name": "chrome.exe", "site": "Google Chrome",
        "date_from": "2026-08-25", "date_to": "2026-08-25",
    }, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["level"] == "title"

    names = {item["name"] for item in data["breakdown"]}
    assert names == {"UL - Cash Lobby - Google Chrome", "Другая страница - Google Chrome"}
    assert "Activity Tracker - GitHub" not in names


def test_breakdown_respects_date_range_boundaries(client, auth_headers):
    """date_from и date_to — ВКЛЮЧИТЕЛЬНЫЕ границы: событие ровно на этих
    датах попадает в выборку, на день раньше/позже — не попадает."""
    client.post("/activities/batch", headers=auth_headers, json={"events": [
        {
            "process_name": "chrome.exe", "window_title": "X - Site",
            "started_at": "2026-08-16T10:00:00", "ended_at": "2026-08-16T10:00:10", "duration_seconds": 10.0,
        },  # ДО диапазона — не должно попасть
        {
            "process_name": "chrome.exe", "window_title": "X - Site",
            "started_at": "2026-08-17T10:00:00", "ended_at": "2026-08-17T10:00:20", "duration_seconds": 20.0,
        },  # ровно date_from — должно попасть
        {
            "process_name": "chrome.exe", "window_title": "X - Site",
            "started_at": "2026-08-19T10:00:00", "ended_at": "2026-08-19T10:00:40", "duration_seconds": 40.0,
        },  # ровно date_to — должно попасть
        {
            "process_name": "chrome.exe", "window_title": "X - Site",
            "started_at": "2026-08-20T10:00:00", "ended_at": "2026-08-20T10:01:20", "duration_seconds": 80.0,
        },  # ПОСЛЕ диапазона — не должно попасть
    ]})

    response = client.get("/stats/daily/breakdown", params={
        "process_name": "chrome.exe", "date_from": "2026-08-17", "date_to": "2026-08-19",
    }, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()

    # Только 2 события внутри диапазона (включительно): 20 + 40 = 60.
    # 10 и 80 (за пределами диапазона) не должны попасть в сумму.
    assert data["breakdown"][0]["total_seconds"] == 60.0


def test_breakdown_shows_only_own_data(client):
    """Изоляция пользователей — та же регрессия, что уже закрыта для
    /stats/daily, но для /stats/daily/breakdown до этого не проверялась."""
    client.post("/auth/register", json={"username": "alice2", "password": "pass123456"})
    client.post("/auth/register", json={"username": "bob2", "password": "pass123456"})

    alice_token = client.post("/auth/login", data={"username": "alice2", "password": "pass123456"}).json()["access_token"]
    bob_token = client.post("/auth/login", data={"username": "bob2", "password": "pass123456"}).json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    client.post("/activities/batch", headers=alice_headers, json={"events": [{
        "process_name": "chrome.exe", "window_title": "Alice's page - Google Chrome",
        "started_at": "2026-08-25T10:00:00", "ended_at": "2026-08-25T10:05:00", "duration_seconds": 300.0,
    }]})
    client.post("/activities/batch", headers=bob_headers, json={"events": [{
        "process_name": "chrome.exe", "window_title": "Bob's page - Google Chrome",
        "started_at": "2026-08-25T10:00:00", "ended_at": "2026-08-25T10:10:00", "duration_seconds": 600.0,
    }]})

    alice_breakdown = client.get("/stats/daily/breakdown", params={
        "process_name": "chrome.exe", "date_from": "2026-08-25", "date_to": "2026-08-25",
    }, headers=alice_headers).json()

    # Алиса видит только СВОЮ активность (300 сек), не 900 (свою + чужую)
    assert alice_breakdown["breakdown"][0]["total_seconds"] == 300.0


def test_breakdown_sorts_by_total_seconds_descending(client, auth_headers):
    client.post("/activities/batch", headers=auth_headers, json={"events": [
        {
            "process_name": "chrome.exe", "window_title": "Small - SiteA",
            "started_at": "2026-08-25T10:00:00", "ended_at": "2026-08-25T10:00:10", "duration_seconds": 10.0,
        },
        {
            "process_name": "chrome.exe", "window_title": "Big - SiteB",
            "started_at": "2026-08-25T11:00:00", "ended_at": "2026-08-25T11:01:40", "duration_seconds": 100.0,
        },
        {
            "process_name": "chrome.exe", "window_title": "Medium - SiteC",
            "started_at": "2026-08-25T12:00:00", "ended_at": "2026-08-25T12:00:50", "duration_seconds": 50.0,
        },
    ]})

    response = client.get("/stats/daily/breakdown", params={
        "process_name": "chrome.exe", "date_from": "2026-08-25", "date_to": "2026-08-25",
    }, headers=auth_headers)

    totals = [item["total_seconds"] for item in response.json()["breakdown"]]
    assert totals == sorted(totals, reverse=True)