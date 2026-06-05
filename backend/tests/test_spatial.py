import unittest
import numpy as np
from app.services.spatial import compute_homography_matrix, transform_points, is_point_in_polygon

class TestSpatialUtilities(unittest.TestCase):
    def test_homography_square(self):
        # Define a square in image plane (normalized 0-1)
        # Assuming the ground rectangle in image looks like this:
        src_pts = [
            (0.2, 0.8), # Bottom-left
            (0.8, 0.8), # Bottom-right
            (0.7, 0.5), # Top-right (perspective)
            (0.3, 0.5)  # Top-left (perspective)
        ]
        
        matrix = compute_homography_matrix(src_pts)
        
        # Transform the source points
        transformed = transform_points(matrix, src_pts)
        
        # Expected target points (BEV square 1000x1000)
        # Note: target order in compute_homography_matrix is [0,0], [1000,0], [1000,1000], [0,1000]
        # Wait, I should check the target definition in spatial.py
        # dst = np.array([[0, 0], [target_size, 0], [target_size, target_size], [0, target_size]])
        
        # The first point (0.2, 0.8) should map to (0, 0)
        self.assertAlmostEqual(transformed[0][0], 0.0, delta=1e-3)
        self.assertAlmostEqual(transformed[0][1], 0.0, delta=1e-3)
        
        # The second point (0.8, 0.8) should map to (1000, 0)
        self.assertAlmostEqual(transformed[1][0], 1000.0, delta=1e-3)
        self.assertAlmostEqual(transformed[1][1], 0.0, delta=1e-3)

    def test_point_in_polygon(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(is_point_in_polygon((5, 5), poly))
        self.assertFalse(is_point_in_polygon((15, 5), poly))

if __name__ == "__main__":
    unittest.main()
