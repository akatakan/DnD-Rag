import unittest

from evaluate import page_matches


class PageMatchesTest(unittest.TestCase):
    def test_matches_inclusive_range(self):
        self.assertTrue(page_matches("8", [7, 9]))
        self.assertFalse(page_matches(10, [7, 9]))

    def test_matches_explicit_page(self):
        self.assertTrue(page_matches(4, [4]))


if __name__ == "__main__":
    unittest.main()
