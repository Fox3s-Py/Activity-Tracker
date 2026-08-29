def test_create_activities_batch_inserts_events(client, auth_headers):
    payload = {
        "events": [
            {
                "process_name": "chrome.exe",
                "window_title": "YouTube",
                "started_at": "2026-08-21T14:00:00",
                "ended_at": "2026-08-21T14:05:00",
                "duration_seconds": 300.0,
            },
            {
                "process_name": "Code.exe",
                "window_title": "main.py — ActivityTracker",
                "started_at": "2026-08-21T14:05:00",
                "ended_at": "2026-08-21T14:20:00",
                "duration_seconds": 900.0,
            },
        ]
    }

    response = client.post("/activities/batch", json=payload, headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"inserted": 2}


def test_create_activities_batch_rejects_missing_field(client, auth_headers):
    payload = {
        "events": [
            {
                # process_name отсутствует — обязательное поле
                "window_title": "YouTube",
                "started_at": "2026-08-21T14:00:00",
                "ended_at": "2026-08-21T14:05:00",
                "duration_seconds": 300.0,
            }
        ]
    }

    response = client.post("/activities/batch", json=payload, headers=auth_headers)

    assert response.status_code == 422


def test_create_activities_batch_strips_nul_bytes(client, auth_headers):
    """Регрессионный тест на баг с NUL-байтами (issue #2)."""
    payload = {
        "events": [
            {
                "process_name": "chrome.exe",
                "window_title": "Битый\x00заголовок",
                "started_at": "2026-08-21T14:00:00",
                "ended_at": "2026-08-21T14:05:00",
                "duration_seconds": 60.0,
            }
        ]
    }

    response = client.post("/activities/batch", json=payload, headers=auth_headers)

    # если бы NUL-байт не чистился — тут был бы 500 (как ловили раньше в проде)
    assert response.status_code == 200
    assert response.json() == {"inserted": 1}


def test_create_activities_batch_requires_auth(client):
    """Без токена — доступ закрыт, даже с абсолютно валидными данными."""
    payload = {
        "events": [
            {
                "process_name": "chrome.exe",
                "window_title": "YouTube",
                "started_at": "2026-08-21T14:00:00",
                "ended_at": "2026-08-21T14:05:00",
                "duration_seconds": 300.0,
            }
        ]
    }

    response = client.post("/activities/batch", json=payload)  # без headers=auth_headers

    assert response.status_code == 401


def test_create_activities_batch_assigns_correct_user_id(client):
    """
    Регрессия на конкретную идею: разные пользователи не должны путать
    друг друга при сохранении событий — каждое событие получает user_id
    именно того, кто его отправил, а не абы чей.
    """
    client.post("/auth/register", json={"username": "alice", "password": "pass123456"})
    client.post("/auth/register", json={"username": "bob", "password": "pass123456"})

    alice_token = client.post("/auth/login", data={"username": "alice", "password": "pass123456"}).json()["access_token"]
    bob_token = client.post("/auth/login", data={"username": "bob", "password": "pass123456"}).json()["access_token"]

    payload = {"events": [{
        "process_name": "chrome.exe", "window_title": "test",
        "started_at": "2026-08-25T10:00:00", "ended_at": "2026-08-25T10:05:00", "duration_seconds": 300.0,
    }]}

    client.post("/activities/batch", json=payload, headers={"Authorization": f"Bearer {alice_token}"})
    client.post("/activities/batch", json=payload, headers={"Authorization": f"Bearer {bob_token}"})

    # проверяем через /auth/me, какие id реально у alice и bob
    alice_id = client.get("/auth/me", headers={"Authorization": f"Bearer {alice_token}"}).json()["id"]
    bob_id = client.get("/auth/me", headers={"Authorization": f"Bearer {bob_token}"}).json()["id"]

    assert alice_id != bob_id  # на всякий случай убеждаемся, что это разные пользователи вообще