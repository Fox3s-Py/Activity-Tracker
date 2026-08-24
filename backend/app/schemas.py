"""
Pydantic-схемы — валидация входа/выхода API.
Отдельно от SQLAlchemy-моделей: схемы про "как выглядят данные в API",
модели про "как данные хранятся в БД". Это разные слои, и их специально
не смешивают.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


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

    @field_validator("process_name", "window_title", mode="before")
    @classmethod
    def clean_nul_bytes(cls, value):
        return strip_nul_bytes(value)


class ActivityBatchIn(BaseModel):
    """Пачка интервалов — то, что реально шлёт клиент раз в N минут."""
    events: list[ActivityIn]


class ActivityBatchOut(BaseModel):
    """Ответ сервера после сохранения пачки."""
    inserted: int


class UserCreate(BaseModel):
    """То, что присылает клиент при регистрации."""
    username: str
    password: str


class UserOut(BaseModel):
    """
    То, что сервер возвращает про пользователя. НИКОГДА не включает
    hashed_password — раз поля просто нет в схеме, оно физически не может
    случайно утечь в ответ, даже если кто-то забудет об этом подумать.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool