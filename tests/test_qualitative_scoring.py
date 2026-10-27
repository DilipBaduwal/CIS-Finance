import unittest

from cis_fin.qualitative_scoring import RUBRIC


class QualitativeScoringTests(unittest.TestCase):
    def test_rubric_covers_zero_to_five(self):
        self.assertEqual(set(RUBRIC), set(range(6)))


if __name__ == "__main__":
    unittest.main()
