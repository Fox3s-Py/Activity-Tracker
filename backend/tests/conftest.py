"""
conftest.py — фикстуры pytest, доступные во всех тестах в этой папке.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_db
from app.models import Base


# Тестовая база — SQLite в памяти. Создаётся заново для каждого теста,
# полностью изолирована от реальной PostgreSQL.
#
# StaticPool ОБЯЗАТЕЛЕН для sqlite ":memory:" — без него каждая новая сессия
# открывает НОВОЕ соединение, а SQLite in-memory существует только в рамках
# одного соединения. Без StaticPool create_all() создаст таблицы в одной
# "невидимой" базе, а INSERT из эндпоинта пойдёт уже в другую, пустую —
# отсюда "no such table" при первом же реальном запросе.
TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    """Версия get_db(), которая ходит в тестовую базу вместо боевой."""
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    Base.metadata.create_all(bind=test_engine)  # создаём таблицы в тестовой базе
    app.dependency_overrides[get_db] = override_get_db  # подменяем зависимость

    yield TestClient(app)

    app.dependency_overrides.clear()  # возвращаем как было после теста
    Base.metadata.drop_all(bind=test_engine)  # чистим тестовую базу