from datetime import date

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