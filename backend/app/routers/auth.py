"""
Эндпоинты аутентификации — регистрация/логин по паролю, логин через Telegram.
Отдельный APIRouter, а не прямо в main.py — стандартная практика, когда
эндпоинтов становится много: группируем по смыслу, каждая группа в своём
файле, а main.py просто "подключает" их все вместе.
"""

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    BOT_SERVICE_SECRET,
    DEVICE_CODE_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    generate_device_code,
    get_current_user,
    hash_password,
    hash_token,
    verify_password,
    verify_refresh_token,
)
from app.database import get_db
from app.models import DeviceCode, User
from app.schemas import (
    DeviceConfirmIn,
    DevicePollIn,
    DevicePollOut,
    DeviceStartOut,
    RefreshIn,
    TelegramLoginRequest,
    Token,
    TokenPair,
    UserCreate,
    UserOut,
)

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

    if user is None or user.hashed_password is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": str(user.id)})
    return Token(access_token=access_token)


@router.post("/telegram-login", response_model=Token)
def telegram_login(request: TelegramLoginRequest, db: Session = Depends(get_db)):
    """
    Вход через Telegram — вызывается ботом от имени человека, который ему
    написал. bot_secret доказывает, что запрос реально от нашего бота, а
    не кто попало прислал произвольный telegram_id. Пользователь создаётся
    автоматически при первом обращении, без пароля вообще — вход только
    через Telegram.
    """
    # hmac.compare_digest вместо обычного == — защита от timing-атак:
    # обычное сравнение строк останавливается на первом несовпадающем
    # символе, и по времени ответа можно по кусочкам угадать секрет.
    # compare_digest всегда работает за постоянное время.
    if not hmac.compare_digest(request.bot_secret, BOT_SERVICE_SECRET):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный секрет бота")

    user = db.query(User).filter(User.telegram_id == request.telegram_id).first()

    if user is None:
        user = User(telegram_id=request.telegram_id)
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = create_access_token(data={"sub": str(user.id)})
    return Token(access_token=access_token)


@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    """
    Возвращает данные того, чей токен передан в запросе. Заодно это удобный
    способ проверить у себя, что токен вообще валиден — если он битый или
    истёк, сюда даже не долетишь, get_current_user сам вернёт 401.
    """
    return current_user


# --- Device flow: код на клиенте -> подтверждение в Telegram -> опрос -> токены ---


@router.post("/device/start", response_model=DeviceStartOut)
def device_start(db: Session = Depends(get_db)):
    """Клиент (трей-приложение) запрашивает новый код для показа пользователю."""
    code = generate_device_code()
    device_code = DeviceCode(code=code, status="pending", created_at=datetime.now(timezone.utc))
    db.add(device_code)
    db.commit()

    return DeviceStartOut(code=code, expires_in_seconds=DEVICE_CODE_EXPIRE_MINUTES * 60)


@router.post("/device/confirm")
def device_confirm(request: DeviceConfirmIn, db: Session = Depends(get_db)):
    """
    Бот вызывает это, когда пользователь прислал ему код. bot_secret —
    та же защита, что и в /auth/telegram-login: доказывает, что запрос
    реально от нашего бота.
    """
    if not hmac.compare_digest(request.bot_secret, BOT_SERVICE_SECRET):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный секрет бота")

    device_code = db.query(DeviceCode).filter(DeviceCode.code == request.code).first()
    if device_code is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Код не найден")

    expires_at = device_code.created_at + timedelta(minutes=DEVICE_CODE_EXPIRE_MINUTES)
    if datetime.now(timezone.utc) > expires_at.replace(tzinfo=timezone.utc):
        db.delete(device_code)
        db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Код истёк, запроси новый")

    user = db.query(User).filter(User.telegram_id == request.telegram_id).first()
    if user is None:
        user = User(telegram_id=request.telegram_id)
        db.add(user)
        db.commit()
        db.refresh(user)

    device_code.status = "confirmed"
    device_code.user_id = user.id
    db.commit()

    return {"detail": "Подтверждено"}


@router.post("/device/poll", response_model=DevicePollOut)
def device_poll(request: DevicePollIn, db: Session = Depends(get_db)):
    """
    Клиент дёргает это раз в пару секунд, пока не получит status='confirmed'.
    Как только код подтверждён — выдаём токены и сразу удаляем код (одноразовый).
    """
    device_code = db.query(DeviceCode).filter(DeviceCode.code == request.code).first()
    if device_code is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Код не найден")

    expires_at = device_code.created_at + timedelta(minutes=DEVICE_CODE_EXPIRE_MINUTES)
    if datetime.now(timezone.utc) > expires_at.replace(tzinfo=timezone.utc):
        db.delete(device_code)
        db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Код истёк, запроси новый")

    if device_code.status == "pending":
        return DevicePollOut(status="pending")

    user = db.query(User).filter(User.id == device_code.user_id).first()
    if user is None:
        # Не должно происходить в норме — подтверждённый код всегда указывает
        # на реального пользователя. Явная проверка лучше, чем упасть ниже
        # с непонятной ошибкой на user.id.
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Пользователь не найден")

    access_token = create_access_token(data={"sub": str(user.id)})
    refresh_token = create_refresh_token()
    user.refresh_token_hash = hash_token(refresh_token)

    db.delete(device_code)  # код одноразовый — использован, сжигаем
    db.commit()

    return DevicePollOut(status="confirmed", access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenPair)
def refresh_access_token(request: RefreshIn, db: Session = Depends(get_db)):
    """
    Обмен refresh_token на новую пару токенов. Возвращаем новый refresh_token
    тоже (ротация) — так украденный старый токен не будет годиться вечно,
    если легитимный клиент успел обменять его первым.
    """
    # Ищем перебором по всем пользователям с непустым refresh_token_hash —
    # цена этого решения объяснена в README/конспекте: при "1 пользователь —
    # 1 устройство" пользователей с активным refresh-токеном мало, полный
    # перебор не проблема. Если добавится поддержка нескольких устройств —
    # это придётся заменить индексом по хэшу токена.
    candidates = db.query(User).filter(User.refresh_token_hash.is_not(None)).all()
    user = next((u for u in candidates if verify_refresh_token(request.refresh_token, u.refresh_token_hash)), None)

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный refresh-токен")

    access_token = create_access_token(data={"sub": str(user.id)})
    new_refresh_token = create_refresh_token()
    user.refresh_token_hash = hash_token(new_refresh_token)
    db.commit()

    return TokenPair(access_token=access_token, refresh_token=new_refresh_token)