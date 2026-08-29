from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    process_name: Mapped[str] = mapped_column(String(255), nullable=False)
    window_title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_activities_started_at", "started_at"),
        Index("ix_activities_user_id", "user_id"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # username/hashed_password — для входа по логину/паролю (может быть NULL,
    # если пользователь пришёл только через Telegram)
    username: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # telegram_id — для входа через Telegram (BigInteger: id Telegram давно
    # превысили диапазон обычного 32-битного Integer)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Храним не сам refresh-токен, а его sha256-хэш — та же логика, что
    # и с паролем: утечка БД не должна означать утечку рабочих токенов.
    # Один пользователь — одно активное устройство (см. решение выше);
    # новый вход через device flow просто перезаписывает старый хэш,
    # тем самым автоматически отзывая предыдущий refresh-токен.
    refresh_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DeviceCode(Base):
    """
    Одноразовый код для device flow — трей-клиент показывает его тебе,
    ты подтверждаешь через Telegram-бота. Живёт недолго (см. EXPIRES_MINUTES
    в app/auth.py) и удаляется/сгорает после использования.
    """
    __tablename__ = "device_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    # NULL, пока код не подтверждён через бота
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | confirmed
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_device_codes_code", "code"),
    )