import unittest
from types import SimpleNamespace

from agent import MultiBookQueryEngine


def scored(book_id: str, score: float):
    return SimpleNamespace(
        score=score,
        node=SimpleNamespace(metadata={"source_book_id": book_id}),
    )


class MultiBookQueryEngineTest(unittest.TestCase):
    def test_balanced_nodes_preserve_both_books(self):
        nodes = MultiBookQueryEngine._balanced_nodes(
            {
                "player": [scored("player", 0.9), scored("player", 0.8)],
                "dm": [scored("dm", 0.7), scored("dm", 0.6)],
            }
        )

        self.assertEqual(len(nodes), 4)
        self.assertEqual(
            {item.node.metadata["source_book_id"] for item in nodes},
            {"player", "dm"},
        )

    def test_rerank_book_preservation_replaces_lowest_node(self):
        player = scored("player", 0.9)
        dm = scored("dm", 0.8)
        nodes = MultiBookQueryEngine._preserve_selected_books(
            [player, player, player, player],
            {"player": [player], "dm": [dm]},
        )

        self.assertIn(dm, nodes)


if __name__ == "__main__":
    unittest.main()
