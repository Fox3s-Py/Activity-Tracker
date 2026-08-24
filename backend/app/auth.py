import os
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
# Дефолт тут только чтобы не падать при импорте, если .env забыт — использовать
# такой ключ на боевом сервере нельзя.
SECRET_KEY = os.getenv("SECRET_KEY", "insecure-dev-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # токен живёт 1 день

# Говорит FastAPI/Swagger, где брать токен для кнопки "Authorize" —
# по сути просто ссылается на наш собственный эндпоинт логина.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


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


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """
    Зависимость для защиты эндпоинтов: Depends(get_current_user).
    Достаёт токен из заголовка Authorization, проверяет его, находит
    пользователя в базе. Если что-то не так — сразу 401, эндпоинт даже
    не начнёт выполняться.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось подтвердить учётные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        # сюда попадём и при истёкшем сроке (exp), и при подделанной подписи
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception

    return user