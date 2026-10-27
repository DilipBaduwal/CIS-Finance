import json
import tempfile
import unittest
from pathlib import Path

from cis_fin.benchmark_versions import create_benchmark_version, create_next_benchmark_version


class NextBenchmarkVersionTests(unittest.TestCase):
    def test_next_version_preserves_old_file_and_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            old_path = Path(directory) / "v1.json"
            new_path = Path(directory) / "v2.json"
            themes = [{"theme": "AI skills", "question": "How are AI skills developed?", "toe_dimension": "organization"}]
            v1 = create_benchmark_version("Information Technology", "2025-2026", ["TCS"], themes, 1, old_path)
            v2 = create_next_benchmark_version(old_path, ["TCS", "INFY"], new_path)
            self.assertEqual(v1["version_number"], 1)
            self.assertEqual(v2["version_number"], 2)
            self.assertEqual(v2["themes"], v1["themes"])
            self.assertTrue(json.loads(old_path.read_text())["immutable"])


if __name__ == "__main__":
    unittest.main()
