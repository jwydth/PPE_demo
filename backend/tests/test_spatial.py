import unittest
import numpy as np
from app.services.spatial import is_point_in_polygon

class TestSpatialUtilities(unittest.TestCase):
    def test_point_in_polygon(self):
        # Square polygon
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        
        # Inside
        self.assertTrue(is_point_in_polygon((5, 5), poly))
        
        # Outside
        self.assertFalse(is_point_in_polygon((15, 5), poly))
        
        # On edge/corner (OpenCV usually returns true for inclusive)
        self.assertTrue(is_point_in_polygon((0, 0), poly))

if __name__ == "__main__":
    unittest.main()
