"""One-off, idempotent data repair: give existing zone violations the severity
the detection pipeline never wrote.

zone_violations.severity was nullable and nothing on the write path ever set
it, so every incursion ever recorded stored NULL. The read layer's
normalize_zone_severity turns NULL into "Medium", and the dashboard's Open
Incidents figure counts Critical + High — so a person walking into a
RESTRICTED zone was detected, stored, and then never appeared in the number an
operator actually watches.

The write path now derives it (see incident_normalization.default_zone_severity
and zone_violation_service). This backfills the rows written before that, using
the same mapping, so history and new incidents agree. Rows that already carry a
severity are left alone. Safe to re-run.
"""

from sqlalchemy import text

from app.db.session import get_engine
from app.services.incident_normalization import _ZONE_TYPE_SEVERITY


def backfill_zone_violation_severity() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for zone_type, severity in _ZONE_TYPE_SEVERITY.items():
            updated = conn.execute(
                text(
                    "UPDATE zone_violations SET severity = :severity "
                    "WHERE severity IS NULL AND upper(zone_type) = :zone_type"
                ),
                {"severity": severity, "zone_type": zone_type},
            ).rowcount
            print(f"ok: {zone_type} -> {severity} ({updated} row(s))")

        # Anything with an unrecognised zone_type keeps the read layer's own
        # default rather than guessing at a new type's seriousness.
        remaining = conn.execute(
            text(
                "UPDATE zone_violations SET severity = 'Medium' WHERE severity IS NULL"
            )
        ).rowcount
        print(f"ok: unrecognised zone types -> Medium ({remaining} row(s))")


if __name__ == "__main__":
    backfill_zone_violation_severity()
