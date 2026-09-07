"""Qdrant clients are shared and closed instead of leaked per request.

`_clients()` used to build a fresh QdrantClient/AsyncQdrantClient pair on every
call with no close anywhere in the codebase, so each /api/rules request
abandoned two clients and their sockets.
"""

import asyncio
import unittest

import retriever


class RetrievalClientLifecycleTest(unittest.TestCase):
    def tearDown(self):
        asyncio.run(retriever.aclose_clients())

    def test_clients_are_reused_across_calls(self):
        first = retriever._clients()
        second = retriever._clients()
        self.assertIs(first, second)

    def test_close_releases_the_pair_and_is_idempotent(self):
        original = retriever._clients()
        asyncio.run(retriever.aclose_clients())
        # A second close must not raise on an already-released pair.
        asyncio.run(retriever.aclose_clients())
        self.assertIsNot(retriever._clients(), original)

    def test_app_lifespan_closes_the_retrieval_clients(self):
        import api.app as app_module

        self.assertIs(
            app_module.aclose_retrieval_clients, retriever.aclose_clients
        )


if __name__ == "__main__":
    unittest.main()
