import unittest
from types import SimpleNamespace

from sources import extract_sources


class ExtractSourcesTest(unittest.TestCase):
    def test_extracts_and_deduplicates_book_pages(self):
        response = SimpleNamespace(
            source_nodes=[
                SimpleNamespace(
                    node=SimpleNamespace(
                        metadata={"source_book": "Player Rules", "page_number": 12}
                    )
                ),
                SimpleNamespace(
                    node=SimpleNamespace(
                        metadata={"source_book": "Player Rules", "page_number": 12}
                    )
                ),
                SimpleNamespace(
                    node=SimpleNamespace(
                        metadata={"source_book": "DM Rules", "source": "8"}
                    )
                ),
            ]
        )

        self.assertEqual(
            extract_sources(response),
            [
                {"book": "Player Rules", "page": 12},
                {"book": "DM Rules", "page": "8"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
