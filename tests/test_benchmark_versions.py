import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from cis_fin.benchmark_versions import create_benchmark_version


class BenchmarkVersionTests(unittest.TestCase):
    def test_version_is_stable_for_same_content(self):
        with TemporaryDirectory() as directory:
            first = create_benchmark_version("Information Technology", "2025-2026", ["TCS"], [], 1, Path(directory) / "a.json")
            second = create_benchmark_version("Information Technology", "2025-2026", ["TCS"], [], 1, Path(directory) / "b.json")
            self.assertEqual(first["benchmark_id"], second["benchmark_id"])


if __name__ == "__main__":
    unittest.main()
