"""
Эндпоинты аутентификации — регистрация, логин (позже).
Отдельный APIRouter, а не прямо в main.py — стандартная практика, когда
эндпоинтов становится много: группируем по смыслу, каждая группа в своём
файле, а main.py просто "подключает" их все вместе.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserOut

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