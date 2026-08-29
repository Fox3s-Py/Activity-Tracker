"""
Разовый скрипт: привязывает все СУЩЕСТВУЮЩИЕ (накопленные до появления
user_id) записи activities к конкретному пользователю.

Запускать ОДИН РАЗ, сразу после миграции, добавившей user_id.
Не трогает записи, у которых user_id уже проставлен — безопасно
запустить повторно, если что-то пошло не так, поменяется 0 строк.

Запуск:
    python scripts/backfill_user_id.py <username>
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Activity, User


def backfill(username: str) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            print(f"Пользователь '{username}' не найден. Сначала зарегистрируй его через /auth/register.")
            return

        orphaned_count = db.query(Activity).filter(Activity.user_id.is_(None)).count()
        print(f"Записей без user_id: {orphaned_count}")

        if orphaned_count == 0:
            print("Нечего привязывать — все записи уже имеют user_id.")
            return

        confirm = input(f"Привязать все {orphaned_count} записей к пользователю '{username}' (id={user.id})? [y/N]: ")
        if confirm.strip().lower() != "y":
            print("Отменено.")
            return

        updated = (
            db.query(Activity)
            .filter(Activity.user_id.is_(None))
            .update({Activity.user_id: user.id})
        )
        db.commit()
        print(f"Готово: обновлено {updated} записей.")

    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python scripts/backfill_user_id.py <username>")
        sys.exit(1)

    backfill(sys.argv[1])