import requests

url = "http://127.0.0.1:8000/activities/batch"

payload = {
    "events": [
        {
            "process_name": "test.exe",
            "window_title": "Тестовое окно",
            "started_at": "2026-08-20T15:00:00",
            "ended_at": "2026-08-20T15:05:00",
            "duration_seconds": 300.0
        }
    ]
}

try:
    response = requests.post(url, json=payload, timeout=5)
    response.raise_for_status()  # выбросит исключение, если код ответа не 2xx
    print("Отправлено:", response.json())
except requests.exceptions.ConnectionError:
    print("Сервер недоступен — сохраняем локально")
    # тут в будущем: запись в SQLite-буфер
except requests.exceptions.Timeout:
    print("Сервер не ответил вовремя — сохраняем локально")
except requests.exceptions.RequestException as e:
    print(f"Другая ошибка запроса: {e}")


