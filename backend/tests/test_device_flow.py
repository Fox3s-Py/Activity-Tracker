import os

BOT_SECRET = os.getenv("BOT_SERVICE_SECRET", "insecure-dev-bot-secret-change-me")


def test_device_start_returns_code(client):
    response = client.post("/auth/device/start")

    assert response.status_code == 200
    data = response.json()
    assert len(data["code"]) == 6
    assert data["expires_in_seconds"] > 0


def test_poll_before_confirm_is_pending(client):
    code = client.post("/auth/device/start").json()["code"]

    response = client.post("/auth/device/poll", json={"code": code})

    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_confirm_rejects_wrong_bot_secret(client):
    code = client.post("/auth/device/start").json()["code"]

    response = client.post("/auth/device/confirm", json={
        "code": code,
        "telegram_id": 111,
        "bot_secret": "wrong-secret",
    })

    assert response.status_code == 401


def test_confirm_unknown_code_returns_404(client):
    response = client.post("/auth/device/confirm", json={
        "code": "ZZZZZZ",
        "telegram_id": 111,
        "bot_secret": BOT_SECRET,
    })

    assert response.status_code == 404


def test_full_device_flow_issues_working_tokens(client):
    code = client.post("/auth/device/start").json()["code"]

    confirm = client.post("/auth/device/confirm", json={
        "code": code,
        "telegram_id": 111,
        "bot_secret": BOT_SECRET,
    })
    assert confirm.status_code == 200

    poll = client.post("/auth/device/poll", json={"code": code})
    assert poll.status_code == 200
    data = poll.json()
    assert data["status"] == "confirmed"
    assert data["access_token"]
    assert data["refresh_token"]

    # Полученный access_token реально работает на защищённом эндпоинте
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] is not None


def test_code_is_burned_after_first_successful_poll(client):
    code = client.post("/auth/device/start").json()["code"]
    client.post("/auth/device/confirm", json={
        "code": code, "telegram_id": 111, "bot_secret": BOT_SECRET,
    })

    first_poll = client.post("/auth/device/poll", json={"code": code})
    assert first_poll.json()["status"] == "confirmed"

    # Тот же код повторно — уже сожжён, должен быть 404, а не второй confirmed
    second_poll = client.post("/auth/device/poll", json={"code": code})
    assert second_poll.status_code == 404


def test_refresh_issues_new_token_pair(client):
    code = client.post("/auth/device/start").json()["code"]
    client.post("/auth/device/confirm", json={
        "code": code, "telegram_id": 111, "bot_secret": BOT_SECRET,
    })
    tokens = client.post("/auth/device/poll", json={"code": code}).json()

    response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["refresh_token"] != tokens["refresh_token"]  # ротация — новый токен, не тот же самый


def test_refresh_rejects_unknown_token(client):
    response = client.post("/auth/refresh", json={"refresh_token": "totally-made-up-token"})

    assert response.status_code == 401


def test_old_refresh_token_stops_working_after_rotation(client):
    code = client.post("/auth/device/start").json()["code"]
    client.post("/auth/device/confirm", json={
        "code": code, "telegram_id": 111, "bot_secret": BOT_SECRET,
    })
    tokens = client.post("/auth/device/poll", json={"code": code}).json()
    old_refresh_token = tokens["refresh_token"]

    # используем токен один раз — он должен ротироваться
    client.post("/auth/refresh", json={"refresh_token": old_refresh_token})

    # повторное использование СТАРОГО токена больше не работает
    reuse_attempt = client.post("/auth/refresh", json={"refresh_token": old_refresh_token})
    assert reuse_attempt.status_code == 401