import unittest

from cis_fin.regression_scoring import RegressionModel


class RegressionScoringTests(unittest.TestCase):
    def test_prediction_is_bounded(self):
        model = RegressionModel((2.0,), 0.0, ("metric",), 10, "Information Technology", "2025-2026")
        self.assertEqual(model.predict([60]), 100.0)


if __name__ == "__main__":
    unittest.main()
