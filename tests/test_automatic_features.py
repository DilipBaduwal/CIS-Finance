import unittest

from cis_fin.automatic_features import build_features_from_document


class AutomaticFeatureTests(unittest.TestCase):
    def test_feature_has_supporting_chunks(self):
        themes = [{"theme": "AI governance", "question": "How does the company govern AI risk?"}]
        records = [{"chunk_id": "c1", "chunk_text": "The company has an AI governance committee and risk controls."}]
        result = build_features_from_document(themes, records)
        self.assertEqual(result["theme_0"]["score"], 1.0)
        self.assertEqual(result["theme_0"]["supporting_chunk_ids"], ["c1"])


if __name__ == "__main__":
    unittest.main()
