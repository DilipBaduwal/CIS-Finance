import unittest

from cis_fin.benchmark_discovery import BenchmarkChunk, discover_theme_clusters


class BenchmarkDiscoveryTests(unittest.TestCase):
    def test_limits_per_toe_and_counts_distinct_companies(self):
        chunks = [
            BenchmarkChunk("a", "TCS", 1, 1, "ai", "technology", [1.0, 0.0]),
            BenchmarkChunk("b", "INFY", 1, 1, "ai", "technology", [0.99, 0.01]),
            BenchmarkChunk("c", "TCS", 2, 2, "ai", "technology", [0.98, 0.02]),
            BenchmarkChunk("d", "WIPRO", 1, 1, "people", "organization", [0.0, 1.0]),
        ]
        clusters = discover_theme_clusters(chunks, similarity_threshold=0.9, maximum_per_dimension=1)
        technology = next(cluster for cluster in clusters if cluster.toe_dimension == "technology")
        self.assertEqual(technology.support_count, 2)
        self.assertEqual(len([cluster for cluster in clusters if cluster.toe_dimension == "technology"]), 1)


if __name__ == "__main__":
    unittest.main()
