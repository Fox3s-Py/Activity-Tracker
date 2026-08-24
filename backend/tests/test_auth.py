def test_register_creates_user(client):
    response = client.post("/auth/register", json={
        "username": "fox3s",
        "password": "supersecret123",
    })

    assert response.status_code == 201
    data = response.json()

    assert data["username"] == "fox3s"
    assert data["is_admin"] is False
    assert "id" in data
    assert "hashed_password" not in data  # пароль (даже хэш) не должен утекать в ответ
    assert "password" not in data


def test_register_rejects_duplicate_username(client):
    payload = {"username": "fox3s", "password": "supersecret123"}

    first = client.post("/auth/register", json=payload)
    assert first.status_code == 201

    second = client.post("/auth/register", json=payload)
    assert second.status_code == 400


def test_login_returns_valid_token(client):
    client.post("/auth/register", json={"username": "fox3s", "password": "supersecret123"})

    response = client.post("/auth/login", data={
        "username": "fox3s",
        "password": "supersecret123",
    })

    assert response.status_code == 200
    data = response.json()

    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Расшифровываем токен и проверяем, что внутри реально лежит правильный username
    from app.auth import ALGORITHM, SECRET_KEY
    from jose import jwt

    payload = jwt.decode(data["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "fox3s"
    assert "exp" in payload


def test_login_rejects_wrong_password(client):
    client.post("/auth/register", json={"username": "fox3s", "password": "supersecret123"})

    response = client.post("/auth/login", data={
        "username": "fox3s",
        "password": "wrongpassword",
    })

    assert response.status_code == 401


def test_login_rejects_unknown_username(client):
    response = client.post("/auth/login", data={
        "username": "ghost",
        "password": "whatever",
    })

    assert response.status_code == 401


def test_me_returns_current_user_with_valid_token(client):
    client.post("/auth/register", json={"username": "fox3s", "password": "supersecret123"})
    login_response = client.post("/auth/login", data={
        "username": "fox3s",
        "password": "supersecret123",
    })
    token = login_response.json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["username"] == "fox3s"


def test_me_rejects_missing_token(client):
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_rejects_garbage_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer this.is.not.a.valid.token"})
    assert response.status_code == 401