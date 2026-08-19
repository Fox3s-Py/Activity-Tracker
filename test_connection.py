import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL) # type: ignore

with engine.connect() as connection:
    result = connection.execute(text("SELECT version();"))
    print("Подключение успешно!")
    print(result.fetchone())