import unittest

from app.services.spatial import is_point_in_polygon


class TestSpatialUtilities(unittest.TestCase):
    def test_point_in_polygon(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]

        self.assertTrue(is_point_in_polygon((5, 5), poly))
        self.assertFalse(is_point_in_polygon((15, 5), poly))
        self.assertTrue(is_point_in_polygon((0, 0), poly))


if __name__ == "__main__":
    unittest.main()
