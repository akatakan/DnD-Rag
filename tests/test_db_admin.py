import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from api.db_admin import copy_database, verify_database
from api.store import GameStore


def _open_store_process(path: str) -> tuple[str, int]:
    store = GameStore(
        Path(path), auth_pepper="shared-process-start-pepper"
    )
    with store.connect() as db:
        return (
            db.execute("PRAGMA journal_mode").fetchone()[0],
            db.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0],
        )


class DatabaseAdminTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        self.store = GameStore(self.source)
        self.created = self.store.create_game(
            "Backup Campaign", "Morgan", "human"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_online_backup_and_new_path_restore_preserve_data(self):
        backup = self.root / "backups" / "campaign.db"
        backup_info = copy_database(self.source, backup)
        self.assertGreater(backup_info["schema_version"], 0)
        self.assertEqual(verify_database(backup), backup_info)

        restored = self.root / "restored" / "game.db"
        restored_info = copy_database(backup, restored)
        self.assertEqual(
            restored_info["schema_version"],
            backup_info["schema_version"],
        )
        restored_store = GameStore(restored)
        auth = restored_store.authenticate(self.created["token"])
        self.assertIsNotNone(auth)
        self.assertEqual(
            restored_store.game(auth.game_id)["name"],
            "Backup Campaign",
        )

    def test_copy_never_overwrites_existing_target(self):
        target = self.root / "existing.db"
        target.write_bytes(b"keep-me")
        with self.assertRaises(FileExistsError):
            copy_database(self.source, target)
        self.assertEqual(target.read_bytes(), b"keep-me")

    def test_copy_fails_closed_when_target_appears_during_publish(self):
        target = self.root / "raced.db"

        def target_wins(_temporary, destination):
            Path(destination).write_bytes(b"other-process")
            raise FileExistsError("target raced")

        with patch("api.db_admin.os.link", side_effect=target_wins):
            with self.assertRaises(FileExistsError):
                copy_database(self.source, target)
        self.assertEqual(target.read_bytes(), b"other-process")

    def test_copy_does_not_delete_target_replaced_during_verification(self):
        target = self.root / "replaced.db"
        source_info = verify_database(self.source)

        def replace_published(path):
            Path(path).unlink()
            Path(path).write_bytes(b"replacement-owned-by-other-process")
            return {
                **source_info,
                "path": str(path),
            }

        with patch(
            "api.db_admin.verify_database",
            side_effect=replace_published,
        ):
            with self.assertRaisesRegex(ValueError, "degistirildi"):
                copy_database(self.source, target)
        self.assertEqual(
            target.read_bytes(),
            b"replacement-owned-by-other-process",
        )

    def test_verify_rejects_corrupt_or_non_tetsu_database(self):
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not sqlite")
        with self.assertRaisesRegex(ValueError, "Gecerli"):
            verify_database(corrupt)

        empty = self.root / "empty.db"
        with closing(sqlite3.connect(empty)):
            pass
        with self.assertRaisesRegex(ValueError, "Gecerli"):
            verify_database(empty)

        with closing(sqlite3.connect(self.source)) as db:
            db.execute(
                "UPDATE schema_migrations SET name = 'tampered' WHERE version = 1"
            )
            db.commit()
        with self.assertRaisesRegex(ValueError, "migration metadata"):
            verify_database(self.source)

    def test_verify_rejects_latest_schema_with_missing_table(self):
        with self.store.connect() as db:
            db.execute("DROP TABLE map_transients")
        with self.assertRaisesRegex(ValueError, "tablolari eksik"):
            verify_database(self.source)

    def test_store_enables_wal_and_busy_timeout(self):
        with self.store.connect() as db:
            mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = db.execute(
                "PRAGMA busy_timeout"
            ).fetchone()[0]
            synchronous = db.execute(
                "PRAGMA synchronous"
            ).fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        self.assertEqual(busy_timeout, 10_000)
        self.assertEqual(synchronous, 2)

    def test_concurrent_store_startup_keeps_schema_and_wal_valid(self):
        path = self.root / "concurrent-start.db"

        def open_store(_index):
            opened = GameStore(path, auth_pepper="shared-start-pepper")
            with opened.connect() as db:
                return (
                    db.execute("PRAGMA journal_mode").fetchone()[0],
                    db.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(open_store, range(4)))
        self.assertEqual({mode for mode, _ in results}, {"wal"})
        self.assertEqual(len({version for _, version in results}), 1)

    def test_multi_process_startup_serializes_migrations(self):
        path = self.root / "process-start.db"
        with ProcessPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(
                _open_store_process,
                [str(path)] * 3,
            ))
        self.assertEqual({mode for mode, _ in results}, {"wal"})
        self.assertEqual(len({version for _, version in results}), 1)
        self.assertEqual(
            verify_database(path)["schema_version"],
            results[0][1],
        )


if __name__ == "__main__":
    unittest.main()
