import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from sqlmodel import SQLModel
import app.models  # noqa: F401
from app.db.session import get_engine

def reset_db_and_tables() -> None:
    engine = get_engine()
    print("Dropping all existing tables...")
    SQLModel.metadata.drop_all(engine)
    print("Creating all tables based on current SQLModel schema...")
    SQLModel.metadata.create_all(engine)
    print("PostgreSQL tables recreated successfully.")

if __name__ == "__main__":
    reset_db_and_tables()
