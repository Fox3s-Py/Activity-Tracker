import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))  # чтобы видеть пакет app/ из backend/

import os
from dotenv import load_dotenv
from app.models import Base
from sqlalchemy import create_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL) # type: ignore

Base.metadata.create_all(engine)

print("Таблицы созданы")