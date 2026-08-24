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