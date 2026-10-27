import json
import tempfile
import unittest
from pathlib import Path

from cis_fin.benchmark_update import score_deltas


class BenchmarkUpdateTests(unittest.TestCase):
    def test_score_deltas(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "old.json"
            new = Path(directory) / "new.json"
            old.write_text(json.dumps({"predictions": [{"company": "TCS", "predicted_score": 50}]}))
            new.write_text(json.dumps({"predictions": [{"company": "TCS", "predicted_score": 55}]}))
            self.assertEqual(score_deltas(old, new)[0]["change"], 5)


if __name__ == "__main__":
    unittest.main()
