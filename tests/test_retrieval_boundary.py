"""The retrieval stack is optional, and the table starts without it.

"Kurala sor" is built on llama-index and Qdrant. None of that is needed to
open a map, roll a die or keep a character sheet, so `api.retrieval` keeps the
heavy imports inside its calls: importing `api.app` must not drag the stack in,
and a missing stack must answer 503 in Turkish instead of crashing the table.

Both halves are guarded here because both are invisible in normal use -- the
stack is installed in CI, so a boundary that quietly collapsed back into a
module-level import would look perfectly healthy until someone deployed
without it.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.app as api_app
import api.retrieval
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.realtime import ConnectionManager
from api.retrieval import RetrievalUnavailable
from api.store import GameStore

REPO_ROOT = Path(__file__).resolve().parents[1]


class RulesEndpointWithoutRetrievalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        self.dm = self.client.post(
            "/api/games",
            json={"name": "Kural Masasi", "dm_name": "DM", "dm_mode": "human"},
        ).json()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def ask(self):
        return self.client.post(
            "/api/rules",
            headers={"Authorization": f"Bearer {self.dm['token']}"},
            json={"question": "Opportunity attack nasil calisir?"},
        )

    def test_a_missing_retrieval_stack_answers_503_in_turkish(self):
        message = (
            "Kural motoru bu kurulumda yok. `uv sync` ile retrieval "
            "bagimliliklarini kurun."
        )

        def unavailable(*_args, **_kwargs):
            raise RetrievalUnavailable(message)

        with patch.object(api_app, "query_rules_engine", unavailable):
            response = self.ask()

        # 503 and not 500: the table is fine, this one surface is not.
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], message)

    def test_the_endpoint_asks_the_retrieval_module_and_nothing_else(self):
        # If someone re-imports `agent` directly into `api.app`, the 503 path
        # above still passes while the boundary is gone. Pin the seam.
        self.assertIs(api_app.query_rules_engine, api.retrieval.ask)
        self.assertIs(api_app.aclose_retrieval, api.retrieval.aclose)


class RetrievalModuleTest(unittest.TestCase):
    """`api.retrieval` turns an absent install into an answerable error."""

    def absent_stack(self):
        # A None entry in sys.modules is exactly what a failed import leaves
        # behind, and `import agent` on it raises ImportError -- so this
        # simulates "not installed" without uninstalling anything.
        return patch.dict(sys.modules, {"agent": None})

    def test_ask_raises_retrieval_unavailable_with_a_turkish_message(self):
        with self.absent_stack(), self.assertRaises(RetrievalUnavailable) as caught:
            api.retrieval.ask(
                "Kalkan AC'yi ne kadar arttirir?",
                game_context="",
                response_mode="rules",
            )

        self.assertIn("Kural motoru bu kurulumda yok", str(caught.exception))

    def test_is_available_reports_the_absence(self):
        with self.absent_stack():
            self.assertFalse(api.retrieval.is_available())
        self.assertTrue(api.retrieval.is_available())

    def test_aclose_is_silent_when_nothing_was_ever_opened(self):
        with patch.dict(sys.modules, {"retriever": None}):
            asyncio.run(api.retrieval.aclose())


class AppImportStaysLightTest(unittest.TestCase):
    def test_importing_the_app_does_not_import_the_retrieval_stack(self):
        # This is the whole point of the boundary, and it can only be observed
        # in a fresh interpreter: this process has already imported llama_index
        # through the other tests, so an in-process sys.modules check would
        # pass no matter what api.app does.
        probe = (
            "import sys; import api.app; "
            "print('llama_index' in sys.modules, 'qdrant_client' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "False False",
            f"api.app pulled the retrieval stack in at import time: {result.stdout!r}",
        )


if __name__ == "__main__":
    unittest.main()
