"""
Диагностика (ничего не меняет в БД): показывает текущее состояние вокруг
привязки telegram_id — сколько пользователей, у кого сколько activities,
чтобы понять, можно ли просто удалить пустого Telegram-дубликата или
сначала нужно перенести его записи.

Запуск:
    python scripts/inspect_telegram_state.py <username> <telegram_id>

<username>    — логин старого аккаунта (тот, у которого 7446+ записей)
<telegram_id> — твой реальный telegram_id, которым уже тестировал бота
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Activity, User


def inspect(username: str, telegram_id: int) -> None:
    db = SessionLocal()
    try:
        old_user = db.query(User).filter(User.username == username).first()
        tg_user = db.query(User).filter(User.telegram_id == telegram_id).first()

        print("=== Старый аккаунт (логин/пароль) ===")
        if old_user is None:
            print(f"НЕ НАЙДЕН пользователь с username='{username}'")
        else:
            count = db.query(Activity).filter(Activity.user_id == old_user.id).count()
            print(f"id={old_user.id}, username={old_user.username!r}, telegram_id={old_user.telegram_id}")
            print(f"Записей activities: {count}")

        print("\n=== Аккаунт с этим telegram_id (если есть) ===")
        if tg_user is None:
            print(f"Пользователя с telegram_id={telegram_id} пока нет — конфликта не будет.")
        else:
            count = db.query(Activity).filter(Activity.user_id == tg_user.id).count()
            print(f"id={tg_user.id}, username={tg_user.username!r}, telegram_id={tg_user.telegram_id}")
            print(f"Записей activities: {count}")

            if old_user is not None and tg_user.id == old_user.id:
                print("\nЭто ОДИН И ТОТ ЖЕ пользователь — привязка уже произошла, ничего делать не нужно.")
            elif count == 0:
                print("\n→ У дубликата 0 записей — безопасно просто удалить его и запустить link_telegram_id.py.")
            else:
                print(
                    f"\n→ У дубликата ЕСТЬ {count} записей — прежде чем удалять, "
                    "нужно решить: перенести их на старый аккаунт (id={}) или отбросить "
                    "как тестовый мусор.".format(old_user.id if old_user else "?")
                )

    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Использование: python scripts/inspect_telegram_state.py <username> <telegram_id>")
        sys.exit(1)

    try:
        telegram_id_arg = int(sys.argv[2])
    except ValueError:
        print("telegram_id должен быть числом.")
        sys.exit(1)

    inspect(sys.argv[1], telegram_id_arg)