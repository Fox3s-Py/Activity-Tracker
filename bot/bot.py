import asyncio
import os
from collections import defaultdict
from datetime import date, timedelta

import requests
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "http://127.0.0.1:8000"

bot = Bot(token=TOKEN)  # type: ignore
dp = Dispatcher()

BTN_TODAY = "📊 Статистика за сегодня"
BTN_YESTERDAY = "📅 Статистика за вчера"
BTN_WEEK = "🗓 Статистика за неделю"

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_TODAY)],
        [KeyboardButton(text=BTN_YESTERDAY)],
        [KeyboardButton(text=BTN_WEEK)],
    ],
    resize_keyboard=True,
)

# id сообщений бота в текущей "сессии" просмотра статистики, по чатам —
# чистим их при следующем нажатии на кнопку выбора периода.
# In-memory: сбрасывается при перезапуске бота — это ок для одного пользователя.
chat_history: dict[int, list[int]] = defaultdict(list)


def format_duration(total_seconds: float) -> str:
    """Секунды -> 'Xч Yм Zс'."""
    total = int(total_seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours}ч {minutes}м {seconds}с"


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
            return None


async def clear_chat_history(chat_id: int) -> None:
    """Удаляет предыдущие сообщения бота из текущей 'сессии' просмотра статистики."""
    for msg_id in chat_history[chat_id]:
        try:
            await bot.delete_message(chat_id, msg_id)
        except TelegramBadRequest:
            pass  # сообщение старше 48ч или уже удалено — Telegram не даст удалить, пропускаем
    chat_history[chat_id].clear()


async def track(chat_id: int, message: Message | None) -> None:
    """Запоминает id отправленного ботом сообщения, чтобы удалить его в следующий раз."""
    if message is not None:
        chat_history[chat_id].append(message.message_id)


@dp.error()
async def error_handler(event):
    """Глобальный обработчик: сетевые сбои Telegram — тихо, остальное — падает как обычно."""
    if isinstance(event.exception, TelegramNetworkError):
        print("Временная сетевая ошибка Telegram, пропускаю это обновление")
        return True
    raise event.exception


@dp.message(CommandStart())
async def start_handler(message: Message):
    await safe_answer(
        message,
        "Привет! Я бот Activity Tracker. Выбери период:",
        reply_markup=main_keyboard,
    )


async def send_daily_stats(message: Message, target_date: date, label: str) -> None:
    """Общая логика для кнопок 'сегодня'/'вчера' — просто разная дата на входе."""
    chat_id = message.chat.id
    await clear_chat_history(chat_id)

    try:
        response = requests.get(
            f"{API_URL}/stats/daily",
            params={"target_date": str(target_date)},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException:
        sent = await safe_answer(message, "Не могу достучаться до сервера. Попробуй позже.")
        await track(chat_id, sent)
        return

    stats = data["stats"]

    if not stats:
        sent = await safe_answer(message, f"{label} ({data['date']}) — данных нет.")
        await track(chat_id, sent)
        return

    builder = InlineKeyboardBuilder()
    for item in stats[:10]:
        button_text = f"{item['process_name']} — {format_duration(item['total_seconds'])}"
        builder.button(text=button_text, callback_data=safe_callback_data("breakdown", item["process_name"]))
    builder.adjust(1)

    sent = await safe_answer(message, f"{label} ({data['date']}):", reply_markup=builder.as_markup())
    await track(chat_id, sent)


async def send_weekly_stats(message: Message) -> None:
    chat_id = message.chat.id
    await clear_chat_history(chat_id)

    try:
        response = requests.get(f"{API_URL}/stats/weekly", timeout=5)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException:
        sent = await safe_answer(message, "Не могу достучаться до сервера. Попробуй позже.")
        await track(chat_id, sent)
        return

    stats = data["stats"]

    if not stats:
        sent = await safe_answer(message, "За эту неделю данных нет.")
        await track(chat_id, sent)
        return

    builder = InlineKeyboardBuilder()
    for item in stats[:10]:
        button_text = f"{item['process_name']} — {format_duration(item['total_seconds'])}"
        builder.button(text=button_text, callback_data=safe_callback_data("breakdown", item["process_name"]))
    builder.adjust(1)

    label = f"🗓 Статистика за неделю ({data['week_start']} — {data['week_end']}):"
    sent = await safe_answer(message, label, reply_markup=builder.as_markup())
    await track(chat_id, sent)


@dp.message(F.text == BTN_TODAY)
async def today_handler(message: Message):
    await send_daily_stats(message, date.today(), "📊 Статистика за сегодня")


@dp.message(F.text == BTN_YESTERDAY)
async def yesterday_handler(message: Message):
    await send_daily_stats(message, date.today() - timedelta(days=1), "📅 Статистика за вчера")


@dp.message(F.text == BTN_WEEK)
async def week_handler(message: Message):
    await send_weekly_stats(message)


@dp.callback_query(F.data.startswith("breakdown:"))
async def breakdown_handler(callback: CallbackQuery):
    process_name = callback.data.split(":", 1)[1]  # type: ignore
    chat_id = callback.message.chat.id  # type: ignore

    try:
        response = requests.get(
            f"{API_URL}/stats/daily/breakdown",
            params={"process_name": process_name},
            timeout=5,
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

    sent = await safe_answer(
        callback.message,  # type: ignore
        f"🔍 {process_name} — детализация по сайтам:",
        reply_markup=builder.as_markup(),
    )
    await track(chat_id, sent)
    await callback.answer()


@dp.callback_query(F.data.startswith("title:"))
async def title_handler(callback: CallbackQuery):
    _, process_name, site = callback.data.split(":", 2)  # type: ignore
    chat_id = callback.message.chat.id  # type: ignore

    try:
        response = requests.get(
            f"{API_URL}/stats/daily/breakdown",
            params={"process_name": process_name, "site": site},
            timeout=5,
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

    sent = await safe_answer(callback.message, "\n".join(lines))  # type: ignore
    await track(chat_id, sent)
    await callback.answer()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())