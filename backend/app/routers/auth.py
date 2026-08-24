"""
Эндпоинты аутентификации — регистрация, логин (позже).
Отдельный APIRouter, а не прямо в main.py — стандартная практика, когда
эндпоинтов становится много: группируем по смыслу, каждая группа в своём
файле, а main.py просто "подключает" их все вместе.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.database import get_db
from app.models import User
from app.schemas import Token, UserCreate, UserOut

# prefix — все пути внутри этого роутера автоматически начинаются с /auth,
# не нужно писать "/auth/register" руками в каждом @router.post(...)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    new_user = User(
        username=user_data.username,
        hashed_password=hash_password(user_data.password),
    )

    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        # username уже занят — сработало ограничение unique=True на уровне БД
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пользователь с таким именем уже существует",
        )

    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()

    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.username})
    return Token(access_token=access_token)


@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    """
    Возвращает данные того, чей токен передан в запросе. Заодно это удобный
    способ проверить у себя, что токен вообще валиден — если он битый или
    истёк, сюда даже не долетишь, get_current_user сам вернёт 401.
    """
    return current_user