"""Response shape for the paginated Live Incident Panel feed.

Deliberately its own module rather than an addition to `violation.py`: the page
model needs `BehaviorIncidentRead`, and `fall_detection` already imports
`detection`, which imports `violation` — declaring it there closes that loop
into a circular import. Nothing imports this module back, so it can safely
depend on all three.
"""

from pydantic import BaseModel

from app.schemas.fall_detection import BehaviorIncidentRead
from app.schemas.violation import ViolationReport, ZoneViolation


class SafetyEventsPage(BaseModel):
    """One page of the merged incident feed (PPE + zone + behavior), ordered by
    recency. `items` keeps each category's own payload shape — the client tells
    them apart by their distinguishing fields (violation_type / zone_type /
    behavior_type), exactly as it did when it merged the three list endpoints
    itself."""

    items: list[ViolationReport | ZoneViolation | BehaviorIncidentRead]
    total: int
    page: int
    page_size: int
    total_pages: int
