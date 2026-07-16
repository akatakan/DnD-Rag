import unittest

from llama_index.core.tools import ToolMetadata

from router import RobustBookSelector


class RobustBookSelectorTest(unittest.TestCase):
    def setUp(self):
        self.selector = RobustBookSelector(llm=None)
        self.choices = [
            ToolMetadata(name="PlayerDnDBasicRules", description="player rules"),
            ToolMetadata(name="DMBasicRules", description="monster rules"),
        ]

    def test_parses_book_id(self):
        result = self.selector._parse(
            "BOOKS: PlayerDnDBasicRules", self.choices, "fighter"
        )
        self.assertEqual(result.inds, [0])

    def test_parses_numbered_natural_language_fallback(self):
        result = self.selector._parse(
            "The relevant choice is (2) because it covers monsters.",
            self.choices,
            "red dragon",
        )
        self.assertEqual(result.inds, [1])

    def test_domain_fallback_selects_both_for_comparison(self):
        result = self.selector._parse(
            "not parseable", self.choices, "Compare player and monster hit points"
        )
        self.assertEqual(result.inds, [0, 1])


if __name__ == "__main__":
    unittest.main()
