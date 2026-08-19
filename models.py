from datetime import datetime

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, DateTime, Integer


class Base(DeclarativeBase):
    """Базовый класс — от него SQLAlchemy узнаёт, какие классы вообще являются таблицами."""
    pass


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    process_name: Mapped[str] = mapped_column(String(255), nullable=False)
    window_title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)