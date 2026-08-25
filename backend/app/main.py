import re
import unicodedata
from datetime import date, timedelta

from fastapi import FastAPI, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Activity, User
from app.routers.auth import router as auth_router
from app.schemas import ActivityBatchIn


app = FastAPI()

app.include_router(auth_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/activities/batch")
def create_activities_batch(
    batch: ActivityBatchIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    objects = [
        Activity(
            process_name=event.process_name,
            window_title=event.window_title,
            started_at=event.started_at,
            ended_at=event.ended_at,
            duration_seconds=event.duration_seconds,
        )
        for event in batch.events
    ]

    db.add_all(objects)
    db.commit()

    return {"inserted": len(objects)}


@app.get("/stats/daily")
def get_daily_stats(
    target_date: date = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if target_date is None:
        target_date = date.today()

    results = (
        db.query(
            Activity.process_name,
            func.sum(Activity.duration_seconds).label("total_seconds")
        )
        .filter(func.date(Activity.started_at) == target_date)
        .group_by(Activity.process_name)
        .order_by(func.sum(Activity.duration_seconds).desc())
        .all()
    )

    return {
        "date": str(target_date),
        "stats": [
            {"process_name": row.process_name, "total_seconds": round(row.total_seconds, 1)}
            for row in results
        ]
    }


@app.get("/stats/weekly")
def get_weekly_stats(
    target_date: date = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Агрегация за неделю (понедельник-воскресенье), в которую попадает target_date.
    Если target_date не передан — берётся текущая неделя.
    """
    if target_date is None:
        target_date = date.today()

    # weekday(): понедельник = 0, воскресенье = 6
    week_start = target_date - timedelta(days=target_date.weekday())
    week_end = week_start + timedelta(days=6)

    results = (
        db.query(
            Activity.process_name,
            func.sum(Activity.duration_seconds).label("total_seconds")
        )
        .filter(func.date(Activity.started_at) >= week_start)
        .filter(func.date(Activity.started_at) <= week_end)
        .group_by(Activity.process_name)
        .order_by(func.sum(Activity.duration_seconds).desc())
        .all()
    )

    return {
        "week_start": str(week_start),
        "week_end": str(week_end),
        "stats": [
            {"process_name": row.process_name, "total_seconds": round(row.total_seconds, 1)}
            for row in results
        ]
    }


# Счётчик непрочитанных в заголовке Telegram: "Андрей - (23232)" — дефис может
# быть обычным (-), длинным (–) или средним (—) в зависимости от версии клиента.
TELEGRAM_UNREAD_PATTERN = re.compile(r"\s*[-–—]\s*\(\d+\)\s*$")

# Ещё один счётчик, отдельный от предыдущего — число в скобках В НАЧАЛЕ
# заголовка, например "(3) Милостивая Государыня – (35751)". Постоянно
# меняется точно так же, как и хвостовой счётчик, и создаёт те же дубли.
LEADING_COUNT_PATTERN = re.compile(r"^\(\d+\)\s*")


def clean_telegram_title(window_title: str) -> str:
    """
    Убирает невидимые управляющие символы (категория Юникода 'Cf' — Format:
    LRM/RLM-метки, изоляторы направления текста и т.п. — Telegram добавляет
    их в заголовок непредсказуемо, из-за чего одна и та же переписка может
    попадать в базу как визуально одинаковые, но байтово разные строки) и
    счётчик непрочитанных сообщений: 'Андрей - (23232)' -> 'Андрей'.
    """
    title = "".join(ch for ch in window_title if unicodedata.category(ch) != "Cf")
    title = title.replace("\xa0", " ")  # неразрывный пробел -> обычный
    title = LEADING_COUNT_PATTERN.sub("", title)
    title = TELEGRAM_UNREAD_PATTERN.sub("", title)
    return title.strip()


def extract_site(window_title: str) -> str:
    """
    Достаёт 'сайт' из заголовка окна браузера — берёт хвост после последнего
    ' - ' (у Chrome заголовок обычно вида 'Страница - Сайт').
    Fallback — весь title целиком, если паттерн не подошёл (не браузер,
    или сайт не проставляет суффикс).

    Заодно чистит счётчик непрочитанных Telegram — для Telegram весь
    заголовок и есть 'сайт' (нет паттерна 'Страница - Приложение'), так что
    после очистки каждый собеседник/чат становится отдельным пунктом
    breakdown вместо мешанины из-за постоянно меняющегося числа.
    """
    if not window_title:
        return "Без названия"

    cleaned = clean_telegram_title(window_title)

    parts = cleaned.rsplit(" - ", 1)
    return parts[1].strip() if len(parts) == 2 else cleaned.strip()


@app.get("/stats/daily/breakdown")
def get_daily_breakdown(
    process_name: str,
    site: str | None = Query(default=None),
    date_from: date = Query(default=None),
    date_to: date = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Drill-down внутри одного приложения за диапазон дат [date_from, date_to]:
      - без ?site=...   -> группировка по сайту (вытащен из window_title)
      - с ?site=...     -> конкретные заголовки внутри этого сайта

    Диапазон, а не одна дата — чтобы этот же эндпоинт работал и для
    detail-view из дневной статистики (date_from == date_to), и для
    недельной (date_from = понедельник, date_to = воскресенье).
    """
    if date_from is None:
        date_from = date.today()
    if date_to is None:
        date_to = date_from

    rows = (
        db.query(Activity.window_title, Activity.duration_seconds)
        .filter(func.date(Activity.started_at) >= date_from)
        .filter(func.date(Activity.started_at) <= date_to)
        .filter(Activity.process_name == process_name)
        .all()
    )

    aggregated: dict[str, float] = {}

    if site is None:
        # Уровень 2: группировка по сайту
        for row in rows:
            key = extract_site(row.window_title)
            aggregated[key] = aggregated.get(key, 0) + row.duration_seconds
        level = "site"
    else:
        # Уровень 3: конкретные заголовки внутри выбранного сайта
        for row in rows:
            if extract_site(row.window_title) != site:
                continue
            aggregated[row.window_title] = aggregated.get(row.window_title, 0) + row.duration_seconds
        level = "title"

    sorted_items = sorted(aggregated.items(), key=lambda x: x[1], reverse=True)

    return {
        "date_from": str(date_from),
        "date_to": str(date_to),
        "process_name": process_name,
        "level": level,
        "site": site,
        "breakdown": [
            {"name": name, "total_seconds": round(total, 1)}
            for name, total in sorted_items
        ]
    }