import asyncio
import os

import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "http://127.0.0.1:8000"

bot = Bot(token=TOKEN)  # type: ignore
dp = Dispatcher()


def format_duration(total_seconds: float) -> str:
    """Секунды -> 'Xч Yм'."""
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    return f"{hours}ч {minutes}м"


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

    builder = InlineKeyboardBuilder()
    for item in stats[:10]:  # топ-10, чтобы клавиатура не была огромной
        button_text = f"{item['process_name']} — {format_duration(item['total_seconds'])}"
        builder.button(text=button_text, callback_data=f"breakdown:{item['process_name']}")

    builder.adjust(1)  # по одной кнопке в ряд

    await message.answer(
        f"📊 Статистика за {data['date']}:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.startswith("breakdown:"))
async def breakdown_handler(callback: CallbackQuery):
    process_name = callback.data.split(":", 1)[1] # type: ignore

    try:
        response = requests.get(
            f"{API_URL}/stats/daily/breakdown",
            params={"process_name": process_name},
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException:
        await callback.answer("Не могу достучаться до сервера.", show_alert=True)
        return

    breakdown = data["breakdown"]

    if not breakdown:
        await callback.answer("Нет данных для детализации.", show_alert=True)
        return

    lines = [f"🔍 {process_name} — детализация по сайтам:\n"]
    for item in breakdown[:10]:
        lines.append(f"{item['name']} — {format_duration(item['total_seconds'])}")

    await callback.message.answer("\n".join(lines)) # type: ignore
    await callback.answer()  # убирает "часики" на кнопке в Telegram


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())