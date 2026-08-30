"""One-off, idempotent schema patch + data repair: gives every incident table
an `area_zone_id` and fills it in for rows written before the column existed.

There's no migration framework in this project (see app/db/init_db.py — schema
is plain SQLModel.metadata.create_all(), which only creates whole missing
tables and never alters an existing one), so this follows the pattern of
scripts/add_report_language_column.py. Safe to re-run.

Why the repair step exists
--------------------------
Incidents used to resolve their zone at read time by following
camera_id -> cameras.home_zone_id. camera_id is declared ON DELETE SET NULL,
so deleting a camera detached every incident it had ever recorded and the
analytics "Incidents by zone" chart moved that entire history into
"Unassigned". Reassigning a camera's home zone had the same effect in reverse:
it rewrote the zone of incidents that happened before the move.

`area_zone_id` freezes the zone onto the incident when it is written. This
script backfills it for existing rows, and first re-attaches incidents that
were orphaned by a camera deletion: cameras.source_key is UNIQUE and every
incident stores the source_key it came from, so an orphaned row whose
source_key matches a live camera provably came from that camera's stream.

Rows that cannot be attributed are left alone — an uploaded file
(source_key "falling.mp4") has no camera and no zone, and "Unassigned" is the
correct answer for it.
"""

from sqlalchemy import text

from app.db.session import get_engine

# behavior_incidents is included even though its camera_id column is the same
# shape: the read path resolves all three categories through the same helper.
TABLES = ("ppe_violations", "zone_violations", "behavior_incidents")


def add_incident_area_zone() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for table in TABLES:
            conn.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS area_zone_id BIGINT "
                    f"REFERENCES physical_zones(id) ON DELETE SET NULL"
                )
            )
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_area_zone_id "
                    f"ON {table} (area_zone_id)"
                )
            )
            print(f"ok: {table}.area_zone_id")

        for table in TABLES:
            # Re-attach rows orphaned by a camera deletion. source_key is
            # UNIQUE on cameras, so this can only ever match one camera.
            relinked = conn.execute(
                text(
                    f"UPDATE {table} AS t SET camera_id = c.id "
                    f"FROM cameras AS c "
                    f"WHERE t.camera_id IS NULL "
                    f"  AND t.source_key IS NOT NULL "
                    f"  AND t.source_key = c.source_key"
                )
            ).rowcount

            # Backfill the frozen zone from wherever the camera points now.
            # This is the best available answer for historical rows: it is what
            # the read path was computing anyway, minus the ability to lose it.
            filled = conn.execute(
                text(
                    f"UPDATE {table} AS t SET area_zone_id = c.home_zone_id "
                    f"FROM cameras AS c "
                    f"WHERE t.area_zone_id IS NULL "
                    f"  AND t.camera_id = c.id "
                    f"  AND c.home_zone_id IS NOT NULL"
                )
            ).rowcount

            unattributed = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE area_zone_id IS NULL")
            ).scalar_one()
            print(
                f"ok: {table} — re-attached {relinked} orphaned row(s), "
                f"filled {filled} zone(s), {unattributed} still unattributed"
            )


if __name__ == "__main__":
    add_incident_area_zone()
