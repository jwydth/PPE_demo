"""Shared WHERE-clause pieces for the three incident tables' range reads.

These filters have to agree exactly with how UnifiedIncidentService labels a
row, or a zone-filtered query returns rows the feed then renders under a
different zone name (or, worse, drops rows the zone chart counted).
"""

from sqlalchemy import and_, or_
from sqlmodel import select

from app.models.camera import Camera


def area_zone_predicate(model, zone_id: int):
    """Rows belonging to physical zone `zone_id`.

    Mirrors UnifiedIncidentService._zone_for's two steps: the zone frozen onto
    the row at write time wins, and a row with no frozen zone falls back to
    wherever its camera currently points. Without the second arm, any row
    written before area_zone_id existed (or by a backend that predates it)
    would be labelled "Warehouse Intake" in the feed while being invisible to
    a Warehouse Intake filter.
    """
    return or_(
        model.area_zone_id == zone_id,
        and_(
            model.area_zone_id.is_(None),
            model.camera_id.in_(
                select(Camera.id).where(Camera.home_zone_id == zone_id)
            ),
        ),
    )
