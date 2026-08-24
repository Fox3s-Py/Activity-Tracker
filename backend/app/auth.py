import os
from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Секретный ключ для подписи токенов — обязательно из .env, никогда не хардкодить.
# Дефолт тут только чтобы не падать при импорте, если .env забыт — использовать
# такой ключ на боевом сервере нельзя.
SECRET_KEY = os.getenv("SECRET_KEY", "insecure-dev-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # токен живёт 1 день


def hash_password(plain_password: str) -> str:
    """Превращает пароль в необратимый хэш для хранения в базе."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверяет, соответствует ли введённый пароль сохранённому хэшу."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    """
    Создаёт подписанный JWT-токен. 'data' обычно содержит {"sub": username} —
    'sub' (subject) это стандартное поле JWT: "о ком этот токен".
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})  # 'exp' — стандартное поле JWT: срок годности
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)