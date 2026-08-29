import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Секретный ключ для подписи токенов — обязательно из .env, никогда не хардкодить.
SECRET_KEY = os.getenv("SECRET_KEY", "insecure-dev-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # токен живёт 1 день

# Отдельный секрет для бота — доказывает бэкенду "это реально мой бот
# спрашивает токен для этого telegram_id", а не кто попало прислал
# произвольный id. НЕ пароль конкретного человека — общий секрет между
# ботом и бэкендом.
BOT_SERVICE_SECRET = os.getenv("BOT_SERVICE_SECRET", "insecure-dev-bot-secret-change-me")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def hash_password(plain_password: str) -> str:
    """Превращает пароль в необратимый хэш для хранения в базе."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверяет, соответствует ли введённый пароль сохранённому хэшу."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    """
    Создаёт подписанный JWT-токен. 'data' обычно содержит {"sub": str(user.id)}.
    ВАЖНО: 'sub' — это id пользователя, а не username — у пользователей,
    пришедших через Telegram, username может отсутствовать вовсе, а id
    есть всегда, независимо от способа входа.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """
    Зависимость для защиты эндпоинтов: Depends(get_current_user).
    Достаёт токен из заголовка Authorization, проверяет его, находит
    пользователя по id (не по username — см. create_access_token).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось подтвердить учётные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_raw = payload.get("sub")
        if user_id_raw is None:
            raise credentials_exception
        user_id = int(user_id_raw)
    except (JWTError, ValueError):
        # JWTError — истёкший/подделанный токен. ValueError — sub не число
        # (например, токен старого формата с username вместо id).
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception

    return user


# --- Device flow ---

DEVICE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без O/0/I/1 — легко спутать на глаз
DEVICE_CODE_LENGTH = 6
DEVICE_CODE_EXPIRE_MINUTES = 10
REFRESH_TOKEN_EXPIRE_DAYS = 30


def generate_device_code() -> str:
    """Короткий человекочитаемый код — его набирают руками в Telegram."""
    return "".join(secrets.choice(DEVICE_CODE_ALPHABET) for _ in range(DEVICE_CODE_LENGTH))


def hash_token(raw_token: str) -> str:
    """sha256 — не для паролей (нужен bcrypt, медленный намеренно), а для
    уже случайных высокоэнтропийных строк, где важна скорость проверки,
    а не защита от перебора (токен и так не подобрать)."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_refresh_token() -> str:
    """Сырой refresh-токен, который получит клиент. В БД попадёт только
    его хэш (см. hash_token) — это делает вызывающий код."""
    return secrets.token_urlsafe(32)


def verify_refresh_token(raw_token: str, stored_hash: str | None) -> bool:
    """hmac.compare_digest — постоянное время сравнения, та же защита от
    timing-атак, что и для BOT_SERVICE_SECRET выше."""
    if stored_hash is None:
        return False
    return hmac.compare_digest(hash_token(raw_token), stored_hash)