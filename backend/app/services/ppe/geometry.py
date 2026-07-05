"""Bounding-box geometry: area, IoU, overlap, distance, stability."""

from __future__ import annotations

from app.core.config import settings
from app.schemas.detection import BoundingBox


def _area(b: dict) -> float:
    return max(0.0, b["x2"] - b["x1"]) * max(0.0, b["y2"] - b["y1"])


def _inter_area(a: dict, b: dict) -> float:
    x_a = max(a["x1"], b["x1"])
    y_a = max(a["y1"], b["y1"])
    x_b = min(a["x2"], b["x2"])
    y_b = min(a["y2"], b["y2"])
    return max(0.0, x_b - x_a) * max(0.0, y_b - y_a)


def _overlap_ratio(equipment: dict, person: dict) -> float:
    eq_area = _area(equipment)
    if eq_area == 0:
        return 0.0
    return _inter_area(equipment, person) / eq_area


def _is_near_frame_edge(box: BoundingBox, frame_width: int, frame_height: int) -> bool:
    margin_x = frame_width * settings.VIDEO_EDGE_MARGIN_RATIO
    margin_y = frame_height * settings.VIDEO_EDGE_MARGIN_RATIO
    return (
        box.x1 <= margin_x
        or box.y1 <= margin_y
        or box.x2 >= frame_width - margin_x
        or box.y2 >= frame_height - margin_y
    )


def _bbox_height_ratio(box: BoundingBox, frame_height: int) -> float:
    if frame_height <= 0:
        return 0.0
    return max(0.0, box.y2 - box.y1) / frame_height


def _is_bbox_stable(boxes: list[BoundingBox]) -> bool:
    window = max(2, settings.VIDEO_STABILITY_WINDOW_FRAMES)
    if len(boxes) < window:
        return False

    first = boxes[0]
    last = boxes[-1]
    if _center_distance_ratio(first, last) > settings.VIDEO_MAX_CENTER_SHIFT_RATIO:
        return False

    first_area = max(_area(first.model_dump()), 1.0)
    last_area = max(_area(last.model_dump()), 1.0)
    size_change = abs(last_area - first_area) / first_area
    return size_change <= settings.VIDEO_MAX_SIZE_CHANGE_RATIO


def _bbox_iou(a: BoundingBox, b: BoundingBox) -> float:
    a_dict = a.model_dump()
    b_dict = b.model_dump()
    union = _area(a_dict) + _area(b_dict) - _inter_area(a_dict, b_dict)
    if union <= 0:
        return 0.0
    return _inter_area(a_dict, b_dict) / union


def _center_distance_ratio(a: BoundingBox, b: BoundingBox) -> float:
    ax = (a.x1 + a.x2) / 2
    ay = (a.y1 + a.y2) / 2
    bx = (b.x1 + b.x2) / 2
    by = (b.y1 + b.y2) / 2
    distance = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
    diagonal = max(_bbox_diagonal(a), _bbox_diagonal(b), 1.0)
    return distance / diagonal


def _bbox_width(box: BoundingBox) -> float:
    return max(0.0, box.x2 - box.x1)


def _bbox_height(box: BoundingBox) -> float:
    return max(0.0, box.y2 - box.y1)


def _bbox_aspect_ratio(box: BoundingBox) -> float:
    width = max(_bbox_width(box), 1.0)
    return _bbox_height(box) / width


def _bbox_diagonal(box: BoundingBox) -> float:
    return ((box.x2 - box.x1) ** 2 + (box.y2 - box.y1) ** 2) ** 0.5
