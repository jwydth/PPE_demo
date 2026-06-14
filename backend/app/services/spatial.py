import cv2
import numpy as np
from typing import List, Tuple


def is_point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
    """
    Checks if a point is inside a polygon using OpenCV's pointPolygonTest.
    """
    poly = np.array(polygon, dtype=np.float32)
    dist = cv2.pointPolygonTest(poly, point, False)
    return dist >= 0
