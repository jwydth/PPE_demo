"""Server-side pagination for the merged Live Incident Panel feed.

The panel shows PPE violations, zone violations, and behavior incidents as one
list ordered by recency. Those live in three separate tables, so the naive
approach — what the frontend used to do — is to pull the most recent N of each,
concatenate, sort, and slice in the client. That reads three large lists to show
six rows, is capped by whatever N the client asked for (so old pages are simply
unreachable), and cannot report a true total.

This service instead asks the database which rows a page holds, using one
UNION ALL over a three-column projection (category, id, timestamp) that every
table can supply. Ordering, LIMIT and OFFSET are applied to that union, so the
database does the merge against its own occurred_at/started_at indexes and
returns exactly the page's worth of identifiers. Only those rows are then
hydrated into full payloads — at most `page_size` of them, however deep the
page is.

`id DESC` is part of the sort key so incidents sharing a timestamp (bursts on
the same frame are common) have one stable order rather than shifting between
requests and letting a row appear on two pages or none.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends
from sqlalchemy import func, literal, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.behavior_incident import BehaviorIncident
from app.models.ppe_violation import PPEViolation
from app.models.zone_violation import ZoneViolation
from app.repositories import RepositoryError
from app.services.incident_filters import CATEGORIES, severity_filter
from app.schemas.fall_detection import BehaviorIncidentRead
from app.schemas.violation import (
    ViolationReport,
    ZoneViolation as ZoneViolationSchema,
)
from app.services.behavior_incident_service import (
    BehaviorIncidentService,
    get_behavior_incident_service,
)
from app.services.ppe_violation_service import (
    PPEViolationService,
    get_ppe_violation_service,
)
from app.services.zone_violation_service import (
    ZoneViolationService,
    get_zone_violation_service,
)

Category = Literal["ppe", "zone", "behavior"]

SafetyEvent = ViolationReport | ZoneViolationSchema | BehaviorIncidentRead


@dataclass(frozen=True)
class FeedFilters:
    """Empty/None everywhere means "no filter", which is the unfiltered feed.

    Filters are applied inside the union legs — before ORDER BY, LIMIT and
    OFFSET — so a page always holds `page_size` matching rows and `total`
    counts matches rather than everything. Filtering after the fact (the
    obvious shortcut) would produce short pages and a total that disagrees
    with what the list shows.
    """

    categories: tuple[Category, ...] = ()
    severities: tuple[str, ...] = ()
    date_from: datetime | None = None
    date_to: datetime | None = None

    @property
    def active_categories(self) -> tuple[Category, ...]:
        return self.categories or CATEGORIES  # type: ignore[return-value]


@dataclass(frozen=True)
class SafetyEventPage:
    items: list[SafetyEvent]
    total: int
    page: int
    page_size: int
    total_pages: int


@dataclass(frozen=True)
class _FeedRow:
    category: Category
    id: int
    occurred_at: datetime


class IncidentFeedService:
    def __init__(
        self,
        ppe_service: Annotated[PPEViolationService, Depends(get_ppe_violation_service)],
        zone_service: Annotated[
            ZoneViolationService, Depends(get_zone_violation_service)
        ],
        behavior_service: Annotated[
            BehaviorIncidentService, Depends(get_behavior_incident_service)
        ],
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.ppe_service = ppe_service
        self.zone_service = zone_service
        self.behavior_service = behavior_service
        self.session = session

    def get_page(
        self,
        *,
        page: int,
        page_size: int,
        filters: FeedFilters | None = None,
    ) -> SafetyEventPage:
        active = filters or FeedFilters()
        total = self._total(active)
        # Ceiling division, floored at one page so an empty feed still reports
        # "page 1 of 1" rather than "of 0".
        total_pages = max(1, -(-total // page_size))
        # A page number past the end (a stale link, or rows deleted since the
        # page was rendered) clamps to the last page instead of 404ing or
        # returning an empty list the UI would have to special-case.
        current = min(max(page, 1), total_pages)
        rows = self._page_rows(
            offset=(current - 1) * page_size, limit=page_size, filters=active
        )
        return SafetyEventPage(
            items=self._hydrate(rows),
            total=total,
            page=current,
            page_size=page_size,
            total_pages=total_pages,
        )

    def _total(self, filters: FeedFilters) -> int:
        """Counted over the same filtered union the page comes from, so the
        total and the rows can never disagree. Unfiltered, this is three
        indexed COUNT(*)s summed — exact, which the old client-side merge could
        not be once any category exceeded its own fetch limit."""
        if not filters.categories and not filters.severities and (
            filters.date_from is None and filters.date_to is None
        ):
            return (
                self.ppe_service.count_violations()
                + self.zone_service.count_zone_violations()
                + self.behavior_service.count_incidents()
            )
        feed = self._feed_subquery(filters)
        try:
            return int(self.session.exec(select(func.count()).select_from(feed)).one())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not count the incident feed.") from exc

    def _feed_subquery(self, filters: FeedFilters):
        """The merged index: one leg per requested category, each projecting to
        the same (category, id, occurred_at) shape. Behavior incidents call
        their timestamp started_at, which is why the projection renames it."""
        sources = {
            "ppe": (PPEViolation, PPEViolation.occurred_at),
            "zone": (ZoneViolation, ZoneViolation.occurred_at),
            "behavior": (BehaviorIncident, BehaviorIncident.started_at),
        }
        legs = []
        for category in filters.active_categories:
            model, timestamp = sources[category]
            leg = select(
                literal(category).label("category"),
                model.id.label("id"),  # type: ignore[union-attr]
                timestamp.label("occurred_at"),
            )
            if filters.date_from is not None:
                leg = leg.where(timestamp >= filters.date_from)
            if filters.date_to is not None:
                leg = leg.where(timestamp <= filters.date_to)
            predicate = severity_filter(category, list(filters.severities))
            if predicate is not None:
                leg = leg.where(predicate)
            legs.append(leg)
        # union_all needs two or more legs; a single category is just that leg.
        merged = union_all(*legs) if len(legs) > 1 else legs[0]
        return merged.subquery("feed")

    def _page_rows(
        self, *, offset: int, limit: int, filters: FeedFilters
    ) -> list[_FeedRow]:
        feed = self._feed_subquery(filters)
        statement = (
            select(feed.c.category, feed.c.id, feed.c.occurred_at)
            .order_by(feed.c.occurred_at.desc(), feed.c.id.desc())
            .offset(offset)
            .limit(limit)
        )
        try:
            result = self.session.exec(statement).all()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read the incident feed.") from exc
        return [
            _FeedRow(category=row[0], id=row[1], occurred_at=row[2]) for row in result
        ]

    def _hydrate(self, rows: list[_FeedRow]) -> list[SafetyEvent]:
        """One query per category for the page's rows, then re-ordered back
        into the union's order — the three hydrations run independently and
        cannot be relied on to come back sorted."""
        by_category: dict[Category, list[int]] = {"ppe": [], "zone": [], "behavior": []}
        for row in rows:
            by_category[row.category].append(row.id)

        hydrated: dict[tuple[Category, int], SafetyEvent] = {}
        for event in self.ppe_service.get_violations_by_ids(by_category["ppe"]):
            if event.id is not None:
                hydrated[("ppe", event.id)] = event
        for event in self.zone_service.get_zone_violations_by_ids(by_category["zone"]):
            if event.id is not None:
                hydrated[("zone", event.id)] = event
        for event in self.behavior_service.get_incidents_by_ids(
            by_category["behavior"]
        ):
            hydrated[("behavior", event.id)] = event

        # A row missing from `hydrated` was deleted between the index query and
        # the hydration; skipping it yields a short page rather than a 500.
        return [
            hydrated[(row.category, row.id)]
            for row in rows
            if (row.category, row.id) in hydrated
        ]


def get_incident_feed_service(
    service: Annotated[IncidentFeedService, Depends(IncidentFeedService)],
) -> IncidentFeedService:
    return service
