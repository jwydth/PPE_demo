"""Create indexes added to the models but missing from an already-existing DB.

There's no migration framework in this project (see app/db/init_db.py —
schema is plain SQLModel.metadata.create_all(), which only creates whole
tables that don't exist yet and never alters existing ones). This script
covers that gap for new indexes added to already-created tables: it walks
every table's Index objects and creates whichever ones the DB doesn't have
yet, using SQLAlchemy's own checkfirst logic so re-running it is a no-op.
"""

import app.models  # noqa: F401  (populates SQLModel.metadata)
from app.db.session import get_engine
from sqlmodel import SQLModel


def apply_new_indexes() -> None:
    engine = get_engine()
    for table in SQLModel.metadata.tables.values():
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)
            print(f"ok: {index.name}")


if __name__ == "__main__":
    apply_new_indexes()
