import unittest
from cis_fin.benchmark_reporting import cohort_summary


class ReportingTests(unittest.TestCase):
    def test_summary_average_and_rank(self):
        result = cohort_summary({"predictions": [{"company": "A", "predicted_score": 40}, {"company": "B", "predicted_score": 60}]})
        self.assertEqual(result["average"], 50.0)
        self.assertEqual(result["ranked"][0]["company"], "B")


if __name__ == "__main__":
    unittest.main()
