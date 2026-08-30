"""
Pydantic-схемы — валидация входа/выхода API.
Отдельно от SQLAlchemy-моделей: схемы про "как выглядят данные в API",
модели про "как данные хранятся в БД". Это разные слои, и их специально
не смешивают.
"""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def strip_nul_bytes(value: str | None) -> str | None:
    """PostgreSQL категорически отказывается хранить NUL-байты (\\x00) в
    текстовых полях. WinAPI (GetWindowText) иногда отдаёт их в заголовках
    окон — чистим на границе системы, а не полагаемся на то, что клиент
    сам всегда пришлёт чистые данные."""
    if value is None:
        return value
    return value.replace("\x00", "")


class ActivityIn(BaseModel):
    """Один интервал, который присылает клиент."""
    process_name: str
    window_title: str | None = None
    started_at: datetime
    ended_at: datetime
    duration_seconds: float

    # Разумный потолок на одну запись — сутки. Не физический закон, а
    # защита от явно кривых данных (например, сломанные часы клиента).
    # Если появится законный сценарий с более длинными интервалами —
    # значение стоит пересмотреть осознанно, а не поднимать втихую.
    MAX_DURATION_SECONDS: ClassVar[int] = 24 * 60 * 60

    @field_validator("process_name", "window_title", mode="before")
    @classmethod
    def clean_nul_bytes(cls, value):
        return strip_nul_bytes(value)

    @field_validator("duration_seconds")
    @classmethod
    def duration_must_be_sane(cls, value):
        if value < 0:
            raise ValueError("duration_seconds не может быть отрицательным")
        if value > cls.MAX_DURATION_SECONDS:
            raise ValueError(
                f"duration_seconds больше {cls.MAX_DURATION_SECONDS} "
                "(суток) — подозрительно, отклонено"
            )
        return value

    @model_validator(mode="after")
    def ended_not_before_started(self):
        if self.ended_at < self.started_at:
            raise ValueError("ended_at не может быть раньше started_at")
        return self


class ActivityBatchIn(BaseModel):
    """Пачка интервалов — то, что реально шлёт клиент раз в N минут."""
    events: list[ActivityIn]


class ActivityBatchOut(BaseModel):
    """Ответ сервера после сохранения пачки."""
    inserted: int


class UserCreate(BaseModel):
    """То, что присылает клиент при регистрации."""
    # max_length совпадает с колонкой users.username (String(50)) — без
    # этой границы на Pydantic-уровне слишком длинный username улетал бы
    # прямо в БД и падал там уже как DataError вместо аккуратного 422.
    username: str = Field(min_length=3, max_length=50)
    # max_length здесь — не точная граница (её считает байтовая проверка
    # ниже), а дешёвый предварительный отсекатель совсем абсурдного ввода
    # (мегабайты текста в поле пароля) ДО того, как мы вообще станем
    # его кодировать в field_validator.
    password: str = Field(min_length=8, max_length=200)

    # bcrypt (через passlib) физически проверяет только первые 72 БАЙТА
    # пароля — в этой версии не с ошибкой, а МОЛЧА обрезая длинные пароли.
    # Проверено: pwd_context.verify("a"*100, hash) и
    # pwd_context.verify("a"*72 + "мусор", hash) дают одинаковый результат.
    # Явно отклоняем на границе API, а не полагаемся на тихое поведение
    # библиотеки — так пользователь получает понятную ошибку, а не иллюзию
    # того, что весь его длинный пароль учтён.
    MAX_PASSWORD_BYTES: ClassVar[int] = 72

    @field_validator("password")
    @classmethod
    def password_must_fit_bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > cls.MAX_PASSWORD_BYTES:
            raise ValueError(
                f"пароль длиннее {cls.MAX_PASSWORD_BYTES} байт — "
                "bcrypt всё равно проверит только начало, отклонено явно"
            )
        return value


class UserOut(BaseModel):
    """
    То, что сервер возвращает про пользователя. НИКОГДА не включает
    hashed_password — раз поля просто нет в схеме, оно физически не может
    случайно утечь в ответ, даже если кто-то забудет об этом подумать.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str | None = None  # у Telegram-пользователей может отсутствовать
    is_admin: bool


class Token(BaseModel):
    """Ответ на успешный логин."""
    access_token: str
    token_type: str = "bearer"


class TokenPair(Token):
    """Ответ, когда клиенту нужен ещё и refresh_token (device flow, /auth/refresh)."""
    refresh_token: str


class TelegramLoginRequest(BaseModel):
    """То, что бот присылает при логине от имени человека, который ему написал."""
    telegram_id: int
    bot_secret: str


class DeviceStartOut(BaseModel):
    """Ответ клиенту на запрос кода — что показать пользователю."""
    code: str
    expires_in_seconds: int


class DeviceConfirmIn(BaseModel):
    """То, что бот присылает, когда пользователь ввёл код в Telegram."""
    code: str
    telegram_id: int
    bot_secret: str


class DevicePollIn(BaseModel):
    code: str


class DevicePollOut(BaseModel):
    """
    status='pending' — ещё не подтверждено, клиент продолжает опрос.
    status='confirmed' — вот токены, опрос можно останавливать.
    """
    status: str
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str