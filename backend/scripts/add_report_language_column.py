"""One-off, idempotent schema patch: adds the `language` column to
`report_schedules` for an already-existing database.

There's no migration framework in this project (see app/db/init_db.py —
schema is plain SQLModel.metadata.create_all(), which only creates whole
missing tables and never alters an existing one). This mirrors
scripts/apply_new_indexes.py's pattern, but for a column instead of an
index. Safe to re-run — IF NOT EXISTS makes it a no-op on a DB that already
has the column (e.g. one created fresh after this change landed, where
create_all() already included it).
"""

from sqlalchemy import text

from app.db.session import get_engine


def add_report_language_column() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE report_schedules "
                "ADD COLUMN IF NOT EXISTS language VARCHAR(8) NOT NULL DEFAULT 'en'"
            )
        )
    print("ok: report_schedules.language")


if __name__ == "__main__":
    add_report_language_column()
