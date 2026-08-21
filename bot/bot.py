import asyncio
import os

import requests
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramNetworkError
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


def safe_callback_data(prefix: str, *parts: str, max_length: int = 60) -> str:
    """
    Собирает callback_data из частей через ':' и обрезает при необходимости,
    чтобы влезть в лимит Telegram (64 байта). Обрезается последняя часть.
    """
    data = ":".join([prefix, *parts])
    if len(data.encode("utf-8")) <= max_length:
        return data

    *head, last = parts
    while last and len(data.encode("utf-8")) > max_length:
        last = last[:-5]
        data = ":".join([prefix, *head, last])
    return data


async def safe_answer(message: Message, text: str, **kwargs):
    """Отправка сообщения с одной повторной попыткой при сетевой ошибке Telegram."""
    for attempt in range(2):
        try:
            return await message.answer(text, **kwargs)
        except TelegramNetworkError:
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            print(f"Не удалось отправить сообщение после повтора: {text[:50]}...")


@dp.error()
async def error_handler(event):
    """Глобальный обработчик: сетевые сбои Telegram — тихо, остальное — падает как обычно (чтобы не прятать реальные баги)."""
    if isinstance(event.exception, TelegramNetworkError):
        print("Временная сетевая ошибка Telegram, пропускаю это обновление")
        return True
    raise event.exception


@dp.message(CommandStart())
async def start_handler(message: Message):
    await safe_answer(message, "Привет! Я бот Activity Tracker.")


@dp.message(Command("stats"))
async def stats_handler(message: Message):
    try:
        response = requests.get(f"{API_URL}/stats/daily", timeout=5)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException:
        await safe_answer(message, "Не могу достучаться до сервера. Попробуй позже.")
        return

    stats = data["stats"]

    if not stats:
        await safe_answer(message, f"За {data['date']} пока нет данных.")
        return

    builder = InlineKeyboardBuilder()
    for item in stats[:10]:  # топ-10, чтобы клавиатура не была огромной
        button_text = f"{item['process_name']} — {format_duration(item['total_seconds'])}"
        builder.button(text=button_text, callback_data=safe_callback_data("breakdown", item["process_name"]))

    builder.adjust(1)  # по одной кнопке в ряд

    await safe_answer(
        message,
        f"📊 Статистика за {data['date']}:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.startswith("breakdown:"))
async def breakdown_handler(callback: CallbackQuery):
    process_name = callback.data.split(":", 1)[1]  # type: ignore

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

    builder = InlineKeyboardBuilder()
    for item in breakdown[:10]:
        button_text = f"{item['name']} — {format_duration(item['total_seconds'])}"
        builder.button(text=button_text, callback_data=safe_callback_data("title", process_name, item["name"]))
    builder.adjust(1)

    await safe_answer(
        callback.message,  # type: ignore
        f"🔍 {process_name} — детализация по сайтам:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()  # убирает "часики" на кнопке в Telegram


@dp.callback_query(F.data.startswith("title:"))
async def title_handler(callback: CallbackQuery):
    _, process_name, site = callback.data.split(":", 2)  # type: ignore

    try:
        response = requests.get(
            f"{API_URL}/stats/daily/breakdown",
            params={"process_name": process_name, "site": site},
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException:
        await callback.answer("Не могу достучаться до сервера.", show_alert=True)
        return

    breakdown = data["breakdown"]

    if not breakdown:
        await callback.answer("Нет данных.", show_alert=True)
        return

    lines = [f"📄 {site} — конкретные страницы:\n"]
    for item in breakdown[:10]:
        lines.append(f"{item['name']} — {format_duration(item['total_seconds'])}")

    await safe_answer(callback.message, "\n".join(lines))  # type: ignore
    await callback.answer()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())