import unittest
from types import SimpleNamespace

from agent import MultiBookQueryEngine
from llama_index.core.base.response.schema import Response


class FakeRetriever:
    def retrieve(self, query):
        return [
            SimpleNamespace(
                score=0.9,
                node=SimpleNamespace(metadata={"source_book_id": "player"}),
            )
        ]


class FakeSynthesizer:
    def __init__(self):
        self.query = None

    def synthesize(self, query, nodes):
        self.query = query
        return Response(response="Rule answer", source_nodes=nodes, metadata={})


class FakeLLM:
    def __init__(self):
        self.prompt = None

    def complete(self, prompt):
        self.prompt = prompt
        return "Creative answer"


class ResponsePipelineTest(unittest.TestCase):
    def make_engine(self):
        engine = MultiBookQueryEngine.__new__(MultiBookQueryEngine)
        engine._llm = FakeLLM()
        engine._book_titles = {"player": "Player Rules"}
        engine._retrievers = {"player": FakeRetriever()}
        engine._reranker = None
        engine._synthesizer = FakeSynthesizer()
        engine.route = lambda query: (["player"], "selector-result")
        return engine

    def test_injects_memory_and_game_state_only_into_synthesis(self):
        engine = self.make_engine()
        response = engine.query(
            "What is my AC?",
            memory_context="Earlier message",
            game_context="AC 16",
        )
        self.assertEqual(str(response), "Rule answer")
        self.assertIn("Earlier message", engine._synthesizer.query)
        self.assertIn("AC 16", engine._synthesizer.query)

    def test_story_mode_keeps_rule_sources(self):
        engine = self.make_engine()
        response = engine.query(
            "Describe the attack",
            game_context="Hero has 4 HP",
            response_mode="story",
        )
        self.assertEqual(str(response), "Creative answer")
        self.assertEqual(len(response.source_nodes), 1)
        self.assertEqual(response.metadata["rule_answer"], "Rule answer")


if __name__ == "__main__":
    unittest.main()
