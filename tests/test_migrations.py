import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from api.migrations import (
    LATEST_SCHEMA_VERSION,
    _execute_script_atomic,
    apply_migrations,
)
from api.store import GameStore


@contextmanager
def connect(path: Path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "game.db"

    def tearDown(self):
        self.temp.cleanup()

    def versions(self) -> list[int]:
        with connect(self.path) as db:
            return [
                row["version"]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]

    def test_new_database_reaches_latest_version_idempotently(self):
        GameStore(self.path)
        self.assertEqual(
            self.versions(),
            list(range(1, LATEST_SCHEMA_VERSION + 1)),
        )
        with connect(self.path) as db:
            before = [
                tuple(row)
                for row in db.execute(
                    "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
                )
            ]

        GameStore(self.path)

        with connect(self.path) as db:
            after = [
                tuple(row)
                for row in db.execute(
                    "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
                )
            ]
        self.assertEqual(after, before)

    def test_legacy_database_is_upgraded_without_losing_game(self):
        timestamp = "2026-01-01T00:00:00+00:00"
        state = {
            "round": 0,
            "turn_index": 0,
            "encounter_status": "idle",
            "combatants": [],
            "characters": {},
            "scene": {"title": "Legacy", "description": "", "public_notes": ""},
        }
        with connect(self.path) as db:
            db.executescript(
                """
                CREATE TABLE games (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    invite_code TEXT UNIQUE NOT NULL, dm_mode TEXT NOT NULL,
                    state_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE members (
                    id TEXT PRIMARY KEY,
                    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, role TEXT NOT NULL, character_id TEXT,
                    token TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    type TEXT NOT NULL, actor_id TEXT NOT NULL,
                    visibility TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE requests (
                    id TEXT PRIMARY KEY,
                    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    actor_id TEXT NOT NULL, type TEXT NOT NULL,
                    payload_json TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, resolved_at TEXT
                );
                """
            )
            db.execute(
                "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-game",
                    "Legacy Campaign",
                    "LEGACY01",
                    "human",
                    json.dumps(state),
                    timestamp,
                    timestamp,
                ),
            )
            db.execute(
                "INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-dm",
                    "legacy-game",
                    "Morgan",
                    "dm",
                    None,
                    "legacy-token",
                    timestamp,
                ),
            )

        store = GameStore(self.path)
        game = store.game("legacy-game")

        self.assertEqual(game["name"], "Legacy Campaign")
        self.assertEqual(game["state"]["scene"]["title"], "Legacy")
        self.assertEqual(game["state"]["turn_actions"], {})
        self.assertEqual(game["owner_id"], "legacy-dm")
        self.assertEqual(game["active_dm_id"], "legacy-dm")
        self.assertEqual(game["fallback_dm_mode"], "assisted")
        self.assertEqual(game["handover"], {})
        self.assertEqual(game["campaign_id"], "legacy-game")
        self.assertEqual(
            game["active_session_id"], "session-legacy-game-1"
        )
        campaign = store.campaign_for_game("legacy-game")
        session = store.active_session("legacy-game")
        memberships = store.campaign_members("legacy-game")
        self.assertEqual(campaign["owner_id"], "legacy-dm")
        self.assertEqual(campaign["ruleset_version"], "srd-5.2.1")
        self.assertEqual(session["status"], "live")
        self.assertEqual(session["started_at"], timestamp)
        self.assertEqual(
            [member["member_id"] for member in memberships], ["legacy-dm"]
        )
        self.assertIsNotNone(store.authenticate("legacy-token"))
        self.assertIsNotNone(store.join_game("LEGACY01", "Legacy Player"))
        with connect(self.path) as db:
            stored_token = db.execute(
                "SELECT token FROM members WHERE id = 'legacy-dm'"
            ).fetchone()[0]
            stored_invite = db.execute(
                "SELECT invite_code FROM games WHERE id = 'legacy-game'"
            ).fetchone()[0]
            ruleset_history = db.execute(
                """
                SELECT from_version, to_version, reason
                FROM campaign_ruleset_history
                WHERE campaign_id = 'legacy-game'
                """
            ).fetchone()
        self.assertNotEqual(stored_token, "legacy-token")
        self.assertNotEqual(stored_invite, "LEGACY01")
        self.assertTrue(stored_token.startswith("hashed:"))
        self.assertTrue(stored_invite.startswith("hashed:"))
        self.assertEqual(
            tuple(ruleset_history),
            ("srd-5.2", "srd-5.2.1", "migration:8"),
        )
        self.assertEqual(
            self.versions(), list(range(1, LATEST_SCHEMA_VERSION + 1))
        )

    def test_failed_migration_does_not_record_a_version(self):
        with connect(self.path) as db:
            db.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )

        def create_probe(db):
            db.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")

        def fail(_db):
            raise RuntimeError("simulated migration failure")

        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(RuntimeError):
                apply_migrations(
                    db,
                    migrations=((1, "probe", create_probe), (2, "fail", fail)),
                )
            db.rollback()
        finally:
            db.close()

        with connect(self.path) as db:
            versions = list(db.execute("SELECT version FROM schema_migrations"))
            probe = db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'probe'"
            ).fetchone()
        self.assertEqual(versions, [])
        self.assertIsNone(probe)

    def test_atomic_script_parses_same_line_statements_and_rolls_back(self):
        db = sqlite3.connect(self.path, isolation_level=None)
        db.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                _execute_script_atomic(
                    db,
                    "CREATE TABLE probe (id INTEGER); "
                    "INSERT INTO probe VALUES (1); "
                    "INSERT INTO missing_table VALUES (2);",
                )
            db.rollback()
        finally:
            db.close()
        with connect(self.path) as db:
            self.assertIsNone(db.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'probe'
                """
            ).fetchone())

    def test_atomic_script_keeps_trigger_body_together(self):
        db = sqlite3.connect(self.path)
        try:
            _execute_script_atomic(
                db,
                "CREATE TABLE source (id INTEGER); "
                "CREATE TABLE audit (id INTEGER); "
                "CREATE TRIGGER source_audit AFTER INSERT ON source "
                "BEGIN INSERT INTO audit VALUES (NEW.id); END;",
            )
            db.execute("INSERT INTO source VALUES (7)")
            self.assertEqual(
                db.execute("SELECT id FROM audit").fetchone()[0], 7
            )
        finally:
            db.close()

    def test_newer_database_version_is_rejected(self):
        with connect(self.path) as db:
            db.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?)",
                (999, "future_schema", "2026-01-01T00:00:00+00:00"),
            )

        with self.assertRaisesRegex(RuntimeError, "daha yeni"):
            GameStore(self.path)


if __name__ == "__main__":
    unittest.main()
