import numpy as np
import cv2
from typing import List, Tuple

def compute_homography_matrix(source_points: List[Tuple[float, float]], target_size: int = 1000) -> np.ndarray:
    """
    Computes the homography matrix from 4 normalized image points to a BEV square.
    
    source_points: List of 4 (x, y) normalized coordinates (0.0 to 1.0)
    target_size: The resolution of the BEV square (default 1000x1000)
    """
    if len(source_points) != 4:
        raise ValueError("Exactly 4 source points are required for homography computation.")
    
    src = np.array(source_points, dtype=np.float32) * target_size
    
    # Target is a square at the same resolution
    dst = np.array([
        [0, 0],
        [target_size, 0],
        [target_size, target_size],
        [0, target_size]
    ], dtype=np.float32)
    
    matrix = cv2.getPerspectiveTransform(src, dst)
    return matrix

def transform_points(matrix: np.ndarray, points: List[Tuple[float, float]], source_scale: int = 1000) -> List[Tuple[float, float]]:
    """
    Transforms a list of normalized points to BEV space.
    """
    if not points:
        return []
    
    pts = np.array(points, dtype=np.float32).reshape(-1, 1, 2) * source_scale
    transformed = cv2.perspectiveTransform(pts, matrix)
    
    # Return as list of tuples
    return [tuple(p) for p in transformed.reshape(-1, 2)]

def is_point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
    """
    Checks if a point is inside a polygon using OpenCV's pointPolygonTest.
    """
    poly = np.array(polygon, dtype=np.float32)
    dist = cv2.pointPolygonTest(poly, point, False)
    return dist >= 0
