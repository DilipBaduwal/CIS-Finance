import unittest

from cis_fin.benchmark_vectors import load_vectors_by_point_ids


class BenchmarkVectorValidationTests(unittest.TestCase):
    def test_requires_qdrant_configuration(self):
        class Settings:
            qdrant_url = None
            qdrant_api_key = None

        with self.assertRaises(Exception):
            load_vectors_by_point_ids(Settings(), ["one"])


if __name__ == "__main__":
    unittest.main()
