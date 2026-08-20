import asyncio
import os

import requests
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "http://127.0.0.1:8000"

bot = Bot(token=TOKEN)  # type: ignore
dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer("Привет! Я бот Activity Tracker.")


@dp.message(Command("stats"))
async def stats_handler(message: Message):
    try:
        response = requests.get(f"{API_URL}/stats/daily", timeout=5)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException:
        await message.answer("Не могу достучаться до сервера. Попробуй позже.")
        return

    stats = data["stats"]

    if not stats:
        await message.answer(f"За {data['date']} пока нет данных.")
        return

    lines = [f"📊 Статистика за {data['date']}:\n"]
    for item in stats:
        hours = int(item["total_seconds"] // 3600)
        minutes = int((item["total_seconds"] % 3600) // 60)
        lines.append(f"{item['process_name']} — {hours}ч {minutes}м")

    await message.answer("\n".join(lines))


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())