import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from api.store import GameStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CatalogStartupOrderTest(unittest.TestCase):
    def test_corrupt_catalog_cannot_commit_v8_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = root / "game.db"
            store = GameStore(db_path)
            created = store.create_game("Pinned Campaign", "Morgan", "human")

            with closing(sqlite3.connect(db_path)) as db:
                db.execute(
                    "UPDATE campaigns SET ruleset_version = 'srd-5.2' WHERE id = ?",
                    (created["campaign_id"],),
                )
                db.execute("DELETE FROM schema_migrations WHERE version >= 8")
                db.execute("DROP TABLE campaign_ruleset_history")
                db.execute("DROP TABLE character_schema_history")
                db.execute("DROP TABLE character_resource_history")
                db.execute("DROP TABLE character_inventory_history")
                db.commit()

            corrupt_root = root / "rulesets"
            catalog_dir = corrupt_root / "srd-5.2.1"
            catalog_dir.mkdir(parents=True)
            (catalog_dir / "catalog.json").write_text("{broken", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "GAME_DB": str(db_path),
                    "RULESET_ROOT": str(corrupt_root),
                    "PUBLIC_MODE": "false",
                    "AUTH_PEPPER": "",
                }
            )

            result = subprocess.run(
                [sys.executable, "-c", "import api.app"],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CatalogValidationError", result.stderr)
            with closing(sqlite3.connect(db_path)) as db:
                schema_version = db.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
                ruleset_version = db.execute(
                    "SELECT ruleset_version FROM campaigns WHERE id = ?",
                    (created["campaign_id"],),
                ).fetchone()[0]
                history_table = db.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'campaign_ruleset_history'
                    """
                ).fetchone()
            self.assertEqual(schema_version, 7)
            self.assertEqual(ruleset_version, "srd-5.2")
            self.assertIsNone(history_table)


if __name__ == "__main__":
    unittest.main()
