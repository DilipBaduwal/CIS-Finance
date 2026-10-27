import json
import tempfile
import unittest
from pathlib import Path

try:
    import numpy  # noqa: F401
except ImportError:
    numpy = None

from cis_fin.benchmark_versions import create_benchmark_version
from cis_fin.cohort_update import update_cohort


class CohortUpdateTests(unittest.TestCase):
    @unittest.skipIf(numpy is None, "numpy is required for pilot regression")
    def test_matching_company_creates_v2_without_mutating_v1(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            v1 = root / "v1.json"
            create_benchmark_version("Information Technology", "2025-2026", ["TCS", "INFY", "WIPRO"], [], 1, v1)
            matrix = {"industry": "Information Technology", "fiscal_year": "2025-2026", "feature_names": ["theme_0"], "rows": [{"company": c, "feature_vector": [2.0], "features": {"theme_0": 2.0}} for c in ["TCS", "INFY", "WIPRO"]]}
            targets = {"targets": [{"company": c, "target_score": 40.0} for c in ["TCS", "INFY", "WIPRO"]]}
            for name, value in [("matrix", matrix), ("targets", targets)]:
                (root / (name + ".json")).write_text(json.dumps(value))
            result = update_cohort(v1, root / "matrix.json", root / "targets.json", root / "model.json", "NEW", "software", "25-26", {"theme_0": 3}, root / "v2.json", root / "matrix2.json", root / "targets2.json", root / "model2.json")
            self.assertEqual(result["version"]["version_number"], 2)
            self.assertEqual(json.loads(v1.read_text())["companies"], ["INFY", "TCS", "WIPRO"])


if __name__ == "__main__":
    unittest.main()
