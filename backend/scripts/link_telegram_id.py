"""
Разовый скрипт: привязывает реальный telegram_id к УЖЕ СУЩЕСТВУЮЩЕМУ
пользователю (созданному через логин/пароль), чтобы при первом входе
через Telegram-бот не создался новый пользователь с пустой историей.

Запускать ОДИН РАЗ, до первого обращения к боту с этого telegram-аккаунта.
Если telegram_id уже занят другим пользователем — скрипт откажется
перезаписывать (это защита от случайной порчи чужих данных или
повторного запуска после того, как привязка уже случилась естественным
путём через бота).

Запуск:
    python scripts/link_telegram_id.py <username> <telegram_id>
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import User


def link(username: str, telegram_id: int) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            print(f"Пользователь '{username}' не найден.")
            return

        if user.telegram_id is not None:
            print(
                f"У пользователя '{username}' (id={user.id}) уже привязан "
                f"telegram_id={user.telegram_id}. Ничего не меняю."
            )
            return

        conflict = db.query(User).filter(User.telegram_id == telegram_id).first()
        if conflict is not None:
            print(
                f"telegram_id={telegram_id} уже привязан к другому пользователю "
                f"(id={conflict.id}, username={conflict.username!r}). "
                "Скорее всего это тот самый 'пустой' аккаунт, который бот "
                "создал при первом сообщении до этой привязки — сначала "
                "разберись, что с ним делать (перенести данные или удалить), "
                "прежде чем привязывать telegram_id заново."
            )
            return

        confirm = input(
            f"Привязать telegram_id={telegram_id} к пользователю "
            f"'{username}' (id={user.id})? [y/N]: "
        )
        if confirm.strip().lower() != "y":
            print("Отменено.")
            return

        user.telegram_id = telegram_id
        db.commit()
        print(f"Готово: пользователь '{username}' (id={user.id}) теперь входит через Telegram.")

    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Использование: python scripts/link_telegram_id.py <username> <telegram_id>")
        sys.exit(1)

    try:
        telegram_id_arg = int(sys.argv[2])
    except ValueError:
        print("telegram_id должен быть числом.")
        sys.exit(1)

    link(sys.argv[1], telegram_id_arg)