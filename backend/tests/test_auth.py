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
    register_response = client.post("/auth/register", json={"username": "fox3s", "password": "supersecret123"})
    registered_user_id = register_response.json()["id"]

    response = client.post("/auth/login", data={
        "username": "fox3s",
        "password": "supersecret123",
    })

    assert response.status_code == 200
    data = response.json()

    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Расшифровываем токен и проверяем, что внутри реально лежит правильный id
    # (не username — у Telegram-пользователей username может отсутствовать,
    # поэтому идентификатор в токене теперь всегда id)
    from app.auth import ALGORITHM, SECRET_KEY
    from jose import jwt

    payload = jwt.decode(data["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == str(registered_user_id)
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


def test_telegram_login_creates_new_user(client, monkeypatch):
    """Первое обращение с новым telegram_id — пользователь создаётся автоматически."""
    monkeypatch.setattr("app.routers.auth.BOT_SERVICE_SECRET", "test-bot-secret")

    response = client.post("/auth/telegram-login", json={
        "telegram_id": 123456789,
        "bot_secret": "test-bot-secret",
    })

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data

    # Проверяем через /auth/me, что реально создался пользователь с этим telegram_id
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}).json()
    assert me["username"] is None  # у Telegram-пользователя нет username


def test_telegram_login_reuses_existing_user(client, monkeypatch):
    """Повторный логин тем же telegram_id — НЕ создаёт второго пользователя, тот же id."""
    monkeypatch.setattr("app.routers.auth.BOT_SERVICE_SECRET", "test-bot-secret")

    first = client.post("/auth/telegram-login", json={"telegram_id": 555, "bot_secret": "test-bot-secret"})
    second = client.post("/auth/telegram-login", json={"telegram_id": 555, "bot_secret": "test-bot-secret"})

    first_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {first.json()['access_token']}"}).json()["id"]
    second_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {second.json()['access_token']}"}).json()["id"]

    assert first_user_id == second_user_id  # тот же самый пользователь, не задвоился


def test_telegram_login_rejects_wrong_bot_secret(client, monkeypatch):
    monkeypatch.setattr("app.routers.auth.BOT_SERVICE_SECRET", "test-bot-secret")

    response = client.post("/auth/telegram-login", json={
        "telegram_id": 123456789,
        "bot_secret": "totally-wrong-secret",
    })

    assert response.status_code == 401

def test_register_rejects_empty_username(client):
    response = client.post("/auth/register", json={"username": "", "password": "supersecret123"})

    assert response.status_code == 422


def test_register_rejects_empty_password(client):
    response = client.post("/auth/register", json={"username": "validuser", "password": ""})

    assert response.status_code == 422


def test_register_rejects_username_over_50_chars(client):
    """Колонка users.username — String(50); без этой границы на Pydantic-уровне
    слишком длинный username улетел бы прямо в БД."""
    response = client.post("/auth/register", json={"username": "x" * 51, "password": "supersecret123"})

    assert response.status_code == 422


def test_register_rejects_password_over_72_bytes(client):
    """bcrypt проверяет только первые 72 байта пароля — молча, без ошибки.
    Отклоняем явно на границе API, а не полагаемся на тихое поведение библиотеки."""
    response = client.post("/auth/register", json={"username": "validuser", "password": "a" * 100})

    assert response.status_code == 422


def test_register_accepts_password_exactly_72_bytes(client):
    """Граничный случай: ровно 72 байта — на пределе, но ещё валидно."""
    response = client.post("/auth/register", json={"username": "validuser", "password": "a" * 72})

    assert response.status_code == 201


def test_register_rejects_absurdly_long_password(client):
    """Верхний потолок на уровне Pydantic (max_length=200) — дешёвая защита
    от совсем неадекватного ввода, срабатывает раньше байтовой проверки."""
    response = client.post("/auth/register", json={"username": "validuser", "password": "a" * 5000})

    assert response.status_code == 422


def test_me_rejects_token_without_sub(client):
    """Токен технически валиден (правильно подписан), но без sub — get_current_user
    должен явно отклонить, а не упасть с необработанным исключением."""
    from app.auth import ALGORITHM, SECRET_KEY
    from jose import jwt
    from datetime import datetime, timedelta, timezone

    token = jwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET_KEY, algorithm=ALGORITHM,
    )

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_me_rejects_token_for_deleted_user(client, db_session):
    """Валидный токен, но пользователь, на которого он указывает, больше не
    существует в БД (удалён после выдачи токена)."""
    from app.models import User

    register = client.post("/auth/register", json={"username": "ghost", "password": "supersecret123"})
    user_id = register.json()["id"]
    login = client.post("/auth/login", data={"username": "ghost", "password": "supersecret123"})
    token = login.json()["access_token"]

    db_session.query(User).filter(User.id == user_id).delete()
    db_session.commit()

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_me_rejects_token_signed_with_wrong_key(client):
    from app.auth import ALGORITHM
    from jose import jwt
    from datetime import datetime, timedelta, timezone

    token = jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "totally-different-secret-key", algorithm=ALGORITHM,
    )

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_me_rejects_expired_token(client):
    from app.auth import ALGORITHM, SECRET_KEY
    from jose import jwt
    from datetime import datetime, timedelta, timezone

    token = jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        SECRET_KEY, algorithm=ALGORITHM,
    )

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_me_rejects_non_numeric_sub(client):
    """Токен старого формата с username вместо id в sub — комментарий в коде
    прямо называет это осознанным регресс-путём, но теста на него не было."""
    from app.auth import ALGORITHM, SECRET_KEY
    from jose import jwt
    from datetime import datetime, timedelta, timezone

    token = jwt.encode(
        {"sub": "not-a-number", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET_KEY, algorithm=ALGORITHM,
    )

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_verify_refresh_token_returns_false_for_none_hash():
    """
    Прямой юнит-тест на app/auth.py:verify_refresh_token — эта ветка
    недостижима через реальный API: /auth/refresh заранее фильтрует
    пользователей через User.refresh_token_hash.is_not(None), так что
    verify_refresh_token с stored_hash=None никогда не вызывается в
    реальном запросе. Без прямого теста строка осталась бы единственной
    непроверенной во всём модуле аутентификации.
    """
    from app.auth import verify_refresh_token

    assert verify_refresh_token("any-token-value", None) is False