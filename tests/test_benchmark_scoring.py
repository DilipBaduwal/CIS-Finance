import unittest

from cis_fin.benchmark_scoring import benchmark_score, benchmark_summary


class BenchmarkScoringTests(unittest.TestCase):
    def test_higher_is_better(self):
        self.assertEqual(benchmark_score(3, [1, 2, 3]), 100.0)

    def test_lower_is_better(self):
        self.assertEqual(benchmark_score(1, [1, 2, 3], "lower_is_better"), 100.0)

    def test_summary(self):
        self.assertEqual(benchmark_summary([1, 2, 3]), {"median": 2, "average": 2.0})


if __name__ == "__main__":
    unittest.main()
