import unittest

from cis_fin.page_index import IndexEntry, parse_index_lines


class ParseIndexLinesTests(unittest.TestCase):
    def test_ignores_unicode_digit_like_standalone_lines(self):
        self.assertEqual(parse_index_lines(["①"]), [])

    def test_ascii_standalone_page_numbers_still_parse(self):
        self.assertEqual(
            parse_index_lines(["12", "Corporate Overview"]),
            [IndexEntry("Corporate Overview", 12, 12, 1)],
        )


if __name__ == "__main__":
    unittest.main()
