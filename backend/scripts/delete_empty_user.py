"""
Разовый скрипт: удаляет пользователя-дубликата, созданного ботом при
раннем тестовом сообщении (до привязки telegram_id к основному аккаунту).

Безопасность: отказывается удалять, если у пользователя есть хоть одна
запись в activities — сначала перепроверь через inspect_telegram_state.py.

Запуск:
    python scripts/delete_empty_user.py <user_id>
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Activity, User


def delete_empty_user(user_id: int) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            print(f"Пользователь id={user_id} не найден.")
            return

        count = db.query(Activity).filter(Activity.user_id == user_id).count()
        if count > 0:
            print(
                f"У пользователя id={user_id} есть {count} записей activities — "
                "УДАЛЕНИЕ ОТМЕНЕНО. Сначала реши, что делать с этими данными."
            )
            return

        print(
            f"Пользователь id={user_id}, username={user.username!r}, "
            f"telegram_id={user.telegram_id}. Записей activities: 0."
        )
        confirm = input("Точно удалить этого пользователя? [y/N]: ")
        if confirm.strip().lower() != "y":
            print("Отменено.")
            return

        db.delete(user)
        db.commit()
        print(f"Готово: пользователь id={user_id} удалён.")

    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python scripts/delete_empty_user.py <user_id>")
        sys.exit(1)

    try:
        user_id_arg = int(sys.argv[1])
    except ValueError:
        print("user_id должен быть числом.")
        sys.exit(1)

    delete_empty_user(user_id_arg)