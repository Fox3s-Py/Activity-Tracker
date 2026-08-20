from datetime import date, timedelta

from fastapi import FastAPI, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import Activity
from schemas import ActivityBatchIn


app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/activities/batch")
def create_activities_batch(batch: ActivityBatchIn, db: Session = Depends(get_db)):
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
def get_daily_stats(target_date: date = Query(default=None), db: Session = Depends(get_db)):
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
def get_weekly_stats(target_date: date = Query(default=None), db: Session = Depends(get_db)):
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


def extract_site(window_title: str) -> str:
    """
    Достаёт 'сайт' из заголовка окна браузера — берёт хвост после последнего
    ' - ' (у Chrome заголовок обычно вида 'Страница - Сайт').
    Fallback — весь title целиком, если паттерн не подошёл (не браузер,
    или сайт не проставляет суффикс).
    """
    if not window_title:
        return "Без названия"
    parts = window_title.rsplit(" - ", 1)
    return parts[1].strip() if len(parts) == 2 else window_title.strip()


@app.get("/stats/daily/breakdown")
def get_daily_breakdown(
    process_name: str,
    site: str | None = Query(default=None),
    target_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Drill-down внутри одного приложения за день:
      - без ?site=...   -> группировка по сайту (вытащен из window_title)
      - с ?site=...     -> конкретные заголовки внутри этого сайта
    """
    if target_date is None:
        target_date = date.today()

    rows = (
        db.query(Activity.window_title, Activity.duration_seconds)
        .filter(func.date(Activity.started_at) == target_date)
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
        "date": str(target_date),
        "process_name": process_name,
        "level": level,
        "site": site,
        "breakdown": [
            {"name": name, "total_seconds": round(total, 1)}
            for name, total in sorted_items
        ]
    }