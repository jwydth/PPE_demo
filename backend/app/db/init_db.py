from sqlmodel import SQLModel

import app.models  # noqa: F401
from app.db.session import get_engine


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(get_engine())


if __name__ == "__main__":
    create_db_and_tables()
    print("PostgreSQL tables created successfully.")
