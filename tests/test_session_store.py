import tempfile
import unittest
from pathlib import Path

from session_store import SessionStore


class SessionStoreTest(unittest.TestCase):
    def test_persists_messages_memory_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            session_id = store.create_session("Test")
            state = store.load_state(session_id)
            state.character.name = "Riva"
            state.notes = "Castle Ravenloft"
            store.save_state(session_id, state)
            store.add_message(session_id, "user", "Kapıyı açıyorum")
            store.add_message(session_id, "assistant", "Kapı açıldı")

            reopened = SessionStore(Path(directory) / "sessions.db")
            self.assertEqual(reopened.load_state(session_id).character.name, "Riva")
            self.assertEqual(len(reopened.messages(session_id)), 2)
            self.assertIn("Kapıyı açıyorum", reopened.memory_context(session_id))

    def test_delete_session_cascades_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            session_id = store.create_session()
            store.add_message(session_id, "tool", "1d20 = 17")
            store.delete_session(session_id)
            self.assertEqual(store.list_sessions(), [])


if __name__ == "__main__":
    unittest.main()
