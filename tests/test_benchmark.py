import unittest

from cis_fin.benchmark import build_cohorts, normalize_fiscal_year, normalize_industry


class BenchmarkTests(unittest.TestCase):
    def test_normalizes_years(self):
        self.assertEqual(normalize_fiscal_year("25-26"), "2025-2026")
        self.assertEqual(normalize_fiscal_year("2025-2026"), "2025-2026")

    def test_normalizes_it_industry_variants(self):
        self.assertEqual(normalize_industry("Information Technology Services"), "Information Technology")
        self.assertEqual(normalize_industry("IT Consulting & Software"), "Information Technology")

    def test_builds_only_same_industry_year_cohorts(self):
        cohorts = build_cohorts(
            [
                ("TCS", "TCS", "Information Technology", "2025-26"),
                ("INFY", "Infosys", "Information Technology Services", "25-26"),
                ("RIL", "Reliance", "Energy", "2024-25"),
            ]
        )
        self.assertEqual(len(cohorts), 1)
        self.assertEqual(cohorts[0].companies, (("INFY", "Infosys"), ("TCS", "TCS")))


if __name__ == "__main__":
    unittest.main()
