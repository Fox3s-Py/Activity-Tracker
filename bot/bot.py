import asyncio
import os
from collections import defaultdict
from datetime import date, timedelta

import requests
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from formatting import (
    PERIOD_TODAY,
    PERIOD_YESTERDAY,
    PERIOD_WEEK,
    PERIOD_LABELS,
    period_date_range,
    format_duration,
    safe_callback_data,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
BOT_SERVICE_SECRET = os.getenv("BOT_SERVICE_SECRET")

bot = Bot(token=TOKEN)  # type: ignore
dp = Dispatcher()

# --- Аутентификация на бэкенде — токен ОТДЕЛЬНО на каждого telegram-пользователя ---

# Раньше был один общий _current_token для всего бота — теперь словарь,
# потому что разные люди в Telegram должны быть РАЗНЫМИ пользователями
# на бэкенде, каждый со своей статистикой.
_tokens: dict[int, str] = {}


def login(telegram_id: int) -> str | None:
    """
    Логинится на бэкенде от имени конкретного telegram_id, сохраняет токен
    в словаре _tokens. bot_secret доказывает бэкенду, что запрос реально
    от нашего бота (см. BOT_SERVICE_SECRET на бэкенде).
    """
    if not BOT_SERVICE_SECRET:
        print("BOT_SERVICE_SECRET не задан в .env — не могу залогиниться")
        return None

    try:
        response = requests.post(
            f"{API_URL}/auth/telegram-login",
            json={"telegram_id": telegram_id, "bot_secret": BOT_SERVICE_SECRET},
            timeout=5,
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        _tokens[telegram_id] = token
        print(f"Успешный логин для telegram_id={telegram_id}")
        return token
    except requests.exceptions.RequestException as e:
        print(f"Не удалось залогиниться для telegram_id={telegram_id} ({e.__class__.__name__})")
        return None


def authenticated_request(telegram_id: int, method: str, path: str, **kwargs) -> requests.Response | None:
    """
    Единая точка для всех запросов к защищённым эндпоинтам бэкенда, теперь
    ОТ ИМЕНИ конкретного telegram_id. Логинится при первой необходимости,
    если сервер отклонит токен (401) — логинится заново и повторяет запрос
    один раз. Возвращает None, если так и не удалось достучаться.
    """
    token = _tokens.get(telegram_id)
    if token is None:
        token = login(telegram_id)
        if token is None:
            return None

    url = f"{API_URL}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.request(method, url, headers=headers, timeout=5, **kwargs)

        if response.status_code == 401:
            token = login(telegram_id)
            if token is None:
                return None
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.request(method, url, headers=headers, timeout=5, **kwargs)

        return response
    except requests.exceptions.RequestException:
        return None

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
chat_history: dict[int, list[int]] = defaultdict(list)


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


def get_telegram_id(message_or_callback: Message | CallbackQuery) -> int | None:
    """
    message.from_user/callback.from_user типизированы как User | None —
    Telegram теоретически может прислать событие без отправителя (например,
    сообщение от имени канала). На практике для обычной переписки человека
    с ботом это не встречается, но явная проверка честнее, чем # type: ignore.
    """
    user = message_or_callback.from_user
    return user.id if user is not None else None


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


async def send_daily_stats(message: Message, target_date: date, label: str, period_code: str) -> None:
    """Общая логика для кнопок 'сегодня'/'вчера' — просто разная дата и код периода на входе."""
    chat_id = message.chat.id
    telegram_id = get_telegram_id(message)
    if telegram_id is None:
        return
    await clear_chat_history(chat_id)

    response = authenticated_request(telegram_id, "GET", "/stats/daily", params={"target_date": str(target_date)})

    if response is None or not response.ok:
        sent = await safe_answer(message, "Не могу достучаться до сервера. Попробуй позже.")
        await track(chat_id, sent)
        return

    data = response.json()

    stats = data["stats"]

    if not stats:
        sent = await safe_answer(message, f"{label} ({data['date']}) — данных нет.")
        await track(chat_id, sent)
        return

    builder = InlineKeyboardBuilder()
    for item in stats[:10]:
        button_text = f"{item['process_name']} — {format_duration(item['total_seconds'])}"
        builder.button(
            text=button_text,
            callback_data=safe_callback_data("breakdown", period_code, item["process_name"]),
        )
    builder.adjust(1)

    sent = await safe_answer(message, f"{label} ({data['date']}):", reply_markup=builder.as_markup())
    await track(chat_id, sent)


async def send_weekly_stats(message: Message) -> None:
    chat_id = message.chat.id
    telegram_id = get_telegram_id(message)
    if telegram_id is None:
        return
    await clear_chat_history(chat_id)

    response = authenticated_request(telegram_id, "GET", "/stats/weekly")

    if response is None or not response.ok:
        sent = await safe_answer(message, "Не могу достучаться до сервера. Попробуй позже.")
        await track(chat_id, sent)
        return

    data = response.json()

    stats = data["stats"]

    if not stats:
        sent = await safe_answer(message, "За эту неделю данных нет.")
        await track(chat_id, sent)
        return

    builder = InlineKeyboardBuilder()
    for item in stats[:10]:
        button_text = f"{item['process_name']} — {format_duration(item['total_seconds'])}"
        builder.button(
            text=button_text,
            callback_data=safe_callback_data("breakdown", PERIOD_WEEK, item["process_name"]),
        )
    builder.adjust(1)

    label = f"🗓 Статистика за неделю ({data['week_start']} — {data['week_end']}):"
    sent = await safe_answer(message, label, reply_markup=builder.as_markup())
    await track(chat_id, sent)


@dp.message(F.text == BTN_TODAY)
async def today_handler(message: Message):
    await send_daily_stats(message, date.today(), "📊 Статистика за сегодня", PERIOD_TODAY)


@dp.message(F.text == BTN_YESTERDAY)
async def yesterday_handler(message: Message):
    await send_daily_stats(
        message,
        date.today() - timedelta(days=1),
        "📅 Статистика за вчера",
        PERIOD_YESTERDAY,
    )


@dp.message(F.text == BTN_WEEK)
async def week_handler(message: Message):
    await send_weekly_stats(message)


@dp.message(F.text.regexp(r"^[A-Za-z2-9]{6}$"))
async def device_code_handler(message: Message):
    """
    Пользователь прислал 6-символьный код из трей-приложения (device flow).
    Подтверждаем его на бэкенде от имени этого telegram_id.
    """
    telegram_id = get_telegram_id(message)
    if telegram_id is None:
        return

    if not BOT_SERVICE_SECRET:
        await safe_answer(message, "Бот не настроен для подтверждения кода.")
        return

    if message.text is None:  # не должно происходить (см. F.text.regexp), но Pylance не знает об этом гарантии
        return
    code = message.text.strip().upper()

    try:
        response = requests.post(
            f"{API_URL}/auth/device/confirm",
            json={"code": code, "telegram_id": telegram_id, "bot_secret": BOT_SERVICE_SECRET},
            timeout=5,
        )
    except requests.exceptions.RequestException:
        await safe_answer(message, "Не удалось связаться с сервером, попробуй ещё раз.")
        return

    if response.status_code == 200:
        await safe_answer(message, "✅ Подтверждено! Вернись в приложение — вход выполнится автоматически.")
    elif response.status_code == 404:
        await safe_answer(message, "Такой код не найден. Проверь, что ввёл его без опечаток.")
    elif response.status_code == 410:
        await safe_answer(message, "Код истёк. Запроси новый в приложении и попробуй снова.")
    else:
        await safe_answer(message, "Не удалось подтвердить код, попробуй ещё раз.")


@dp.callback_query(F.data.startswith("breakdown:"))
async def breakdown_handler(callback: CallbackQuery):
    _, period_code, process_name = callback.data.split(":", 2)  # type: ignore
    chat_id = callback.message.chat.id  # type: ignore
    telegram_id = get_telegram_id(callback)
    if telegram_id is None:
        return
    date_from, date_to = period_date_range(period_code)

    response = authenticated_request(
        telegram_id, "GET", "/stats/daily/breakdown",
        params={
            "process_name": process_name,
            "date_from": str(date_from),
            "date_to": str(date_to),
        },
    )

    if response is None or not response.ok:
        await callback.answer("Не могу достучаться до сервера.", show_alert=True)
        return

    data = response.json()

    breakdown = data["breakdown"]

    if not breakdown:
        await callback.answer("Нет данных для детализации.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for item in breakdown[:10]:
        button_text = f"{item['name']} — {format_duration(item['total_seconds'])}"
        builder.button(
            text=button_text,
            callback_data=safe_callback_data("title", period_code, process_name, item["name"]),
        )
    builder.adjust(1)

    period_label = PERIOD_LABELS.get(period_code, "")
    sent = await safe_answer(
        callback.message,  # type: ignore
        f"🔍 {process_name} — детализация по сайтам ({period_label}):",
        reply_markup=builder.as_markup(),
    )
    await track(chat_id, sent)
    await callback.answer()


@dp.callback_query(F.data.startswith("title:"))
async def title_handler(callback: CallbackQuery):
    _, period_code, process_name, site = callback.data.split(":", 3)  # type: ignore
    chat_id = callback.message.chat.id  # type: ignore
    telegram_id = get_telegram_id(callback)
    if telegram_id is None:
        return
    date_from, date_to = period_date_range(period_code)

    response = authenticated_request(
        telegram_id, "GET", "/stats/daily/breakdown",
        params={
            "process_name": process_name,
            "site": site,
            "date_from": str(date_from),
            "date_to": str(date_to),
        },
    )

    if response is None or not response.ok:
        await callback.answer("Не могу достучаться до сервера.", show_alert=True)
        return

    data = response.json()

    breakdown = data["breakdown"]

    if not breakdown:
        await callback.answer("Нет данных.", show_alert=True)
        return

    period_label = PERIOD_LABELS.get(period_code, "")
    lines = [f"📄 {site} — конкретные страницы ({period_label}):\n"]
    for item in breakdown[:10]:
        lines.append(f"{item['name']} — {format_duration(item['total_seconds'])}")

    sent = await safe_answer(callback.message, "\n".join(lines))  # type: ignore
    await track(chat_id, sent)
    await callback.answer()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())