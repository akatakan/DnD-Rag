import unittest
from types import SimpleNamespace

from agent import MultiBookQueryEngine


class FakeRetriever:
    def retrieve(self, query):
        return [
            SimpleNamespace(
                score=0.9,
                node=SimpleNamespace(
                    metadata={"source_book_id": "player"},
                ),
            )
        ]


class FakeSynthesizer:
    def synthesize(self, query, nodes):
        return SimpleNamespace(metadata={}, source_nodes=nodes)


class ProgressCallbackTest(unittest.TestCase):
    def test_reports_observable_pipeline_stages(self):
        engine = MultiBookQueryEngine.__new__(MultiBookQueryEngine)
        engine._book_titles = {"player": "Player Rules"}
        engine._retrievers = {"player": FakeRetriever()}
        engine._reranker = None
        engine._synthesizer = FakeSynthesizer()
        engine.route = lambda query: (["player"], "selector-result")
        events = []

        engine.query("How does initiative work?", progress=lambda *event: events.append(event))

        self.assertEqual(
            [stage for stage, _ in events],
            ["routing", "routing", "reading", "reading", "synthesis", "complete"],
        )
        self.assertIn("1 aday parça", events[3][1])


if __name__ == "__main__":
    unittest.main()
