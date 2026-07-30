import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from api.store import GameStore


class MapMigrationTest(unittest.TestCase):
    def test_v25_repairs_missing_scene_and_fog_without_losing_game(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Legacy VTT", "DM", "human")
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "DELETE FROM map_fog_state WHERE game_id = ?",
                    (created["game_id"],),
                )
                db.execute(
                    "DELETE FROM map_scenes WHERE game_id = ?",
                    (created["game_id"],),
                )
                db.execute(
                    "DELETE FROM schema_migrations WHERE version = 25"
                )
                db.commit()

            repaired = GameStore(path)
            auth = repaired.authenticate(created["token"])
            self.assertIsNotNone(auth)
            scene = repaired.map_scene(auth)
            self.assertEqual(scene["revision"], 1)
            self.assertEqual(scene["name"], "Battle Map")
            self.assertFalse(scene["fog"]["enabled"])
            with repaired.connect() as db:
                self.assertEqual(
                    db.execute(
                        """
                        SELECT COUNT(*) FROM schema_migrations
                        WHERE version = 25
                        """
                    ).fetchone()[0],
                    1,
                )

    def test_v25_preserves_existing_scene_fog_and_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            preserved = store.create_game("Preserved", "DM One", "human")
            missing = store.create_game("Missing", "DM Two", "human")
            preserved_auth = store.authenticate(preserved["token"])
            campaign_id = store.game(preserved["game_id"])["campaign_id"]
            with store.connect() as db:
                db.execute(
                    """
                    UPDATE map_scenes
                    SET name = 'Hex Vault', grid_type = 'hex',
                        grid_size_px = 96, revision = 7
                    WHERE game_id = ?
                    """,
                    (preserved["game_id"],),
                )
                db.execute(
                    """
                    UPDATE map_fog_state
                    SET enabled = 1, revision = 5
                    WHERE game_id = ?
                    """,
                    (preserved["game_id"],),
                )
                db.execute(
                    """
                    INSERT INTO map_tokens (
                        id, game_id, campaign_id, combatant_id,
                        owner_member_id, x, y, size_px, revision,
                        created_at, updated_at
                    ) VALUES (
                        'preserved-token', ?, ?, 'preserved-combatant',
                        ?, 32, 48, 64, 3, '2026-01-01', '2026-01-01'
                    )
                    """,
                    (
                        preserved["game_id"],
                        campaign_id,
                        preserved_auth.member_id,
                    ),
                )
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "DELETE FROM map_fog_state WHERE game_id = ?",
                    (missing["game_id"],),
                )
                db.execute(
                    "DELETE FROM map_scenes WHERE game_id = ?",
                    (missing["game_id"],),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 25")
                db.commit()

            repaired = GameStore(path)
            with repaired.connect() as db:
                scene = db.execute(
                    "SELECT * FROM map_scenes WHERE game_id = ?",
                    (preserved["game_id"],),
                ).fetchone()
                fog = db.execute(
                    "SELECT * FROM map_fog_state WHERE game_id = ?",
                    (preserved["game_id"],),
                ).fetchone()
                token = db.execute(
                    "SELECT * FROM map_tokens WHERE id = 'preserved-token'"
                ).fetchone()
                repaired_scene = db.execute(
                    "SELECT * FROM map_scenes WHERE game_id = ?",
                    (missing["game_id"],),
                ).fetchone()
            self.assertEqual(scene["name"], "Hex Vault")
            self.assertEqual(scene["grid_type"], "hex")
            self.assertEqual(scene["revision"], 7)
            self.assertEqual(fog["enabled"], 1)
            self.assertEqual(fog["revision"], 5)
            self.assertEqual(token["revision"], 3)
            self.assertEqual(repaired_scene["name"], "Battle Map")

    def test_v25_corrupt_data_rolls_back_the_whole_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            corrupt = store.create_game("Corrupt", "DM One", "human")
            missing = store.create_game("Missing", "DM Two", "human")
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA ignore_check_constraints = ON")
                db.execute(
                    """
                    UPDATE map_scenes SET grid_type = 'triangle'
                    WHERE game_id = ?
                    """,
                    (corrupt["game_id"],),
                )
                db.execute(
                    "DELETE FROM map_fog_state WHERE game_id = ?",
                    (missing["game_id"],),
                )
                db.execute(
                    "DELETE FROM map_scenes WHERE game_id = ?",
                    (missing["game_id"],),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 25")
                db.commit()

            with self.assertRaisesRegex(
                RuntimeError, "map scene metadata gecersiz"
            ):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertIsNone(
                    db.execute(
                        """
                        SELECT 1 FROM map_scenes WHERE game_id = ?
                        """,
                        (missing["game_id"],),
                    ).fetchone()
                )
                self.assertIsNone(
                    db.execute(
                        """
                        SELECT 1 FROM map_fog_state WHERE game_id = ?
                        """,
                        (missing["game_id"],),
                    ).fetchone()
                )
                self.assertIsNone(
                    db.execute(
                        """
                        SELECT 1 FROM schema_migrations WHERE version = 25
                        """
                    ).fetchone()
                )

    def test_v24_fog_scope_trigger_rejects_cross_campaign(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            first = store.create_game("First", "DM One", "human")
            second = store.create_game("Second", "DM Two", "human")
            with self.assertRaises(sqlite3.IntegrityError):
                with store.connect() as db:
                    db.execute(
                        """
                        UPDATE map_fog_state SET campaign_id = ?
                        WHERE game_id = ?
                        """,
                        (second["game_id"], first["game_id"]),
                    )

    def test_v24_transient_scope_trigger_rejects_cross_game_actor(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            first = store.create_game("First", "DM One", "human")
            second = store.create_game("Second", "DM Two", "human")
            second_auth = store.authenticate(second["token"])
            with self.assertRaises(sqlite3.IntegrityError):
                with store.connect() as db:
                    db.execute(
                        """
                        INSERT INTO map_transients (
                            id, game_id, actor_id, kind, payload_json,
                            expires_at, created_at
                        ) VALUES (
                            'cross-game-ping', ?, ?, 'ping', '{}',
                            '2099-01-01', '2026-01-01'
                        )
                        """,
                        (first["game_id"], second_auth.member_id),
                    )

    def test_v24_revalidation_rejects_corrupt_transient_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Corrupt Signal", "DM", "human")
            auth = store.authenticate(created["token"])
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    """
                    INSERT INTO map_transients (
                        id, game_id, actor_id, kind, payload_json,
                        expires_at, created_at
                    ) VALUES (
                        'corrupt-ping', ?, ?, 'ping', '{"x": -1, "y": 4}',
                        '2099-01-01', '2026-01-01'
                    )
                    """,
                    (auth.game_id, auth.member_id),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 24")
                db.commit()

            with self.assertRaisesRegex(
                RuntimeError, "map transient metadata gecersiz"
            ):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 24"
                ).fetchone()
            self.assertIsNone(applied)

    def test_v24_revalidation_rejects_non_object_transient_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Corrupt Signal", "DM", "human")
            auth = store.authenticate(created["token"])
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    """
                    INSERT INTO map_transients (
                        id, game_id, actor_id, kind, payload_json,
                        expires_at, created_at
                    ) VALUES (
                        'corrupt-signal-list', ?, ?, 'draw', '[]',
                        '2026-01-01T00:00:30+00:00',
                        '2026-01-01T00:00:00+00:00'
                    )
                    """,
                    (auth.game_id, auth.member_id),
                )
                db.execute(
                    "DELETE FROM schema_migrations WHERE version = 24"
                )
                db.commit()

            with self.assertRaisesRegex(
                RuntimeError, "map transient metadata gecersiz"
            ):
                GameStore(path)

    def test_v24_revalidation_rejects_orphan_fog_cell(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            store.create_game("Corrupt Fog", "DM", "human")
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA foreign_keys = OFF")
                db.execute(
                    """
                    INSERT INTO map_fog_cells (
                        game_id, cell_x, cell_y, updated_at
                    ) VALUES (?, 1, 1, '2026-01-01')
                    """,
                    ("missing-game",),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 24")
                db.commit()

            with self.assertRaisesRegex(
                RuntimeError, "map fog cell metadata gecersiz"
            ):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 24"
                ).fetchone()
            self.assertIsNone(applied)

    def test_v23_token_scope_trigger_rejects_cross_game_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            first = store.create_game("First", "DM One", "human")
            second = store.create_game("Second", "DM Two", "human")
            first_auth = store.authenticate(first["token"])
            second_auth = store.authenticate(second["token"])
            campaign_id = store.game(first_auth.game_id)["campaign_id"]
            with self.assertRaises(sqlite3.IntegrityError):
                with store.connect() as db:
                    db.execute(
                        """
                        INSERT INTO map_tokens (
                            id, game_id, campaign_id, combatant_id,
                            owner_member_id, x, y, size_px, revision,
                            created_at, updated_at
                        ) VALUES (
                            'cross-owner-token', ?, ?, 'combatant',
                            ?, 32, 32, 48, 1, '2026-01-01', '2026-01-01'
                        )
                        """,
                        (
                            first_auth.game_id,
                            campaign_id,
                            second_auth.member_id,
                        ),
                    )

    def test_v23_revalidation_rejects_corrupt_token_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Corrupt Token", "DM", "human")
            auth = store.authenticate(created["token"])
            campaign_id = store.game(auth.game_id)["campaign_id"]
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA ignore_check_constraints = ON")
                db.execute(
                    """
                    INSERT INTO map_tokens (
                        id, game_id, campaign_id, combatant_id,
                        owner_member_id, x, y, size_px, revision,
                        created_at, updated_at
                    ) VALUES (
                        'corrupt-token', ?, ?, 'combatant',
                        NULL, -1, 32, 48, 1, '2026-01-01', '2026-01-01'
                    )
                    """,
                    (auth.game_id, campaign_id),
                )
                db.execute(
                    "DELETE FROM schema_migrations WHERE version = 23"
                )
                db.commit()

            with self.assertRaisesRegex(
                RuntimeError, "map token metadata gecersiz"
            ):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 23"
                ).fetchone()
            self.assertIsNone(applied)

    def test_v22_backfills_scene_and_rejects_cross_campaign_uploader(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            first = store.create_game("First", "DM One", "human")
            second = store.create_game("Second", "DM Two", "human")
            first_auth = store.authenticate(first["token"])
            second_auth = store.authenticate(second["token"])
            self.assertEqual(store.map_scene(first_auth)["revision"], 1)

            with self.assertRaises(sqlite3.IntegrityError):
                with store.connect() as db:
                    db.execute(
                        """
                        INSERT INTO map_assets (
                            id, campaign_id, uploader_id, original_name,
                            storage_key, sha256, content_type, byte_size,
                            width, height, created_at
                        ) VALUES (?, ?, ?, 'map.png', ?, ?, 'image/png',
                            100, 64, 64, '2026-01-01T00:00:00Z')
                        """,
                        (
                            "cross-campaign-map",
                            store.game(first_auth.game_id)["campaign_id"],
                            second_auth.member_id,
                            f"{'a' * 64}.png",
                            "a" * 64,
                        ),
                    )

    def test_v22_revalidation_fails_closed_without_recording_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Corrupt", "DM", "human")
            auth = store.authenticate(created["token"])
            campaign_id = store.game(auth.game_id)["campaign_id"]
            with closing(sqlite3.connect(path)) as db:
                db.execute("DROP TRIGGER trg_map_asset_uploader_scope_insert")
                db.execute(
                    """
                    INSERT INTO map_assets (
                        id, campaign_id, uploader_id, original_name,
                        storage_key, sha256, content_type, byte_size,
                        width, height, created_at
                    ) VALUES (
                        'corrupt-map', ?, ?, 'bad.png', 'wrong.png', ?,
                        'image/png', 100, 64, 64, '2026-01-01'
                    )
                    """,
                    (campaign_id, auth.member_id, "b" * 64),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 22")
                db.commit()

            with self.assertRaisesRegex(
                RuntimeError, "map asset metadata gecersiz"
            ):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 22"
                ).fetchone()
            self.assertIsNone(applied)

    def test_v22_revalidation_rejects_corrupt_scene_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Corrupt Scene", "DM", "human")
            auth = store.authenticate(created["token"])
            store.map_scene(auth)
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA ignore_check_constraints = ON")
                db.execute(
                    "UPDATE map_scenes SET grid_type = 'triangle'"
                )
                db.execute(
                    "DELETE FROM schema_migrations WHERE version = 22"
                )
                db.commit()

            with self.assertRaisesRegex(
                RuntimeError, "map scene metadata gecersiz"
            ):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 22"
                ).fetchone()
            self.assertIsNone(applied)


if __name__ == "__main__":
    unittest.main()
