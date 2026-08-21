def test_daily_stats_aggregates_by_process(client):
    # Сначала кладём данные в базу — через тот же /activities/batch,
    # которым уже пользовались в прошлых тестах
    client.post("/activities/batch", json={
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
    response = client.get("/stats/daily", params={"target_date": "2026-08-21"})

    assert response.status_code == 200
    data = response.json()

    # chrome.exe должен схлопнуться в одну строку: 300 + 600 = 900
    stats_by_process = {item["process_name"]: item["total_seconds"] for item in data["stats"]}
    assert stats_by_process["chrome.exe"] == 900.0
    assert stats_by_process["Code.exe"] == 1200.0

    # Code.exe (1200 сек) должен быть выше chrome.exe (900 сек) — сортировка по убыванию
    assert data["stats"][0]["process_name"] == "Code.exe"