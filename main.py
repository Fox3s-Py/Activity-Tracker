from fastapi import FastAPI, Depends
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