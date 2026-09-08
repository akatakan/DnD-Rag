"""Qdrant clients are shared and closed instead of leaked per request.

`_clients()` used to build a fresh QdrantClient/AsyncQdrantClient pair on every
call with no close anywhere in the codebase, so each /api/rules request
abandoned two clients and their sockets.

The shutdown path now runs through `api.retrieval.aclose` instead of importing
`retriever` into `api.app`, so the app can boot without the retrieval stack
installed. The release still has to happen, so it is asserted end to end here:
the lifespan calls the seam, and the seam calls the client pair's own close.
"""

import asyncio
import unittest
from unittest.mock import patch

import api.retrieval
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
        # Driving the real lifespan rather than comparing symbols: the point is
        # that shutting the table down releases the clients, and an identity
        # check would still pass if the `finally` block stopped calling it.
        import api.app as app_module

        closed: list[str] = []

        async def record_close():
            closed.append("aclose")

        async def shut_the_app_down():
            with patch.object(app_module, "aclose_retrieval", record_close):
                async with app_module.app_lifespan(app_module.app):
                    self.assertEqual(closed, [], "closed before shutdown")

        asyncio.run(shut_the_app_down())
        self.assertEqual(closed, ["aclose"])

    def test_the_seam_releases_the_real_client_pair(self):
        # The other half of the chain: `api.retrieval.aclose` must reach
        # `retriever.aclose_clients`, or the lifespan would be closing nothing.
        released: list[str] = []

        async def record_release():
            released.append("aclose_clients")

        with patch.object(retriever, "aclose_clients", record_release):
            asyncio.run(api.retrieval.aclose())

        self.assertEqual(released, ["aclose_clients"])


if __name__ == "__main__":
    unittest.main()
