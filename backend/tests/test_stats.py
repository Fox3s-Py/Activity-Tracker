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