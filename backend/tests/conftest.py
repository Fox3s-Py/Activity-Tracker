"""
conftest.py — специальный файл pytest, не нужно нигде импортировать вручную.
pytest сам находит его и подхватывает всё, что тут определено (фикстуры),
делая их доступными во всех тестах в этой папке.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Тестовый HTTP-клиент — умеет 'стучаться' в приложение без реального сервера и сети."""
    return TestClient(app)