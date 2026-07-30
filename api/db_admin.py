from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path

from api.migrations import MIGRATIONS

_LATEST_TABLES = {
    "auth_configuration", "auth_tokens", "campaign_members",
    "campaign_ruleset_history", "campaigns", "character_action_history",
    "character_drafts", "character_inventory_history",
    "character_resource_history", "character_schema_history",
    "command_receipts", "encounter_drafts", "encounter_undo_history",
    "events", "game_invites", "games", "map_assets", "map_fog_cells",
    "map_fog_state", "map_scenes", "map_tokens", "map_transients",
    "member_dice_preferences", "members", "requests",
    "schema_migrations", "security_audit_events", "session_loot",
    "session_notes", "session_quests", "sessions", "websocket_tickets",
}
_BASE_TABLES = {"games", "members", "events", "requests", "schema_migrations"}


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _target_path(path: Path) -> Path:
    expanded = path.expanduser()
    parent = expanded.parent.resolve()
    return parent / expanded.name


def _file_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat(follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino


def _has_identity(path: Path, identity: tuple[int, int]) -> bool:
    try:
        return _file_identity(path) == identity
    except OSError:
        return False


def _file_fingerprint(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat(follow_symlinks=False)
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _has_fingerprint(
    path: Path, fingerprint: tuple[int, int, int, int]
) -> bool:
    try:
        return _file_fingerprint(path) == fingerprint
    except OSError:
        return False


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Python cannot portably open directory handles for FlushFileBuffers.
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_connection(
    connection: sqlite3.Connection, source: Path
) -> dict[str, int | str]:
    try:
        integrity = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity_check basarisiz: {integrity}")
        migration_rows = connection.execute(
            """
            SELECT version, name FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()
        expected = {
            version: name for version, name, _action in MIGRATIONS
        }
        versions = [int(row[0]) for row in migration_rows]
        if (
            versions != list(range(1, len(versions) + 1))
            or not versions
            or any(expected.get(row[0]) != row[1] for row in migration_rows)
        ):
            raise ValueError("SQLite migration metadata gecersiz.")
        foreign_key_error = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchone()
        if foreign_key_error is not None:
            raise ValueError("SQLite foreign-key tutarliligi gecersiz.")
        tables = {
            row[0] for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }
        required_tables = (
            _LATEST_TABLES
            if versions[-1] == len(MIGRATIONS)
            else _BASE_TABLES
        )
        missing = sorted(required_tables - tables)
        if missing:
            if versions[-1] == len(MIGRATIONS):
                message = "SQLite latest schema tablolari eksik: "
            else:
                message = "SQLite temel schema tablolari eksik: "
            raise ValueError(
                message + ", ".join(missing)
            )
        return {
            "path": str(source),
            "schema_version": versions[-1],
            "migration_count": len(versions),
            "byte_size": source.stat().st_size,
        }
    except sqlite3.DatabaseError as error:
        raise ValueError("Gecerli bir Tetsu SQLite veritabani degil.") from error


def verify_database(path: Path) -> dict[str, int | str]:
    source = _resolved(path)
    if not source.is_file():
        raise ValueError(f"SQLite dosyasi bulunamadi: {source}")
    before = _file_identity(source)
    connection = sqlite3.connect(
        f"{source.as_uri()}?mode=ro", uri=True, timeout=10
    )
    try:
        result = _verify_connection(connection, source)
    finally:
        connection.close()
    if not _has_identity(source, before):
        raise ValueError("SQLite dosyasi dogrulama sirasinda degisti.")
    return result


def copy_database(source: Path, target: Path) -> dict[str, int | str]:
    source_path = _resolved(source)
    target_path = _target_path(target)
    if not source_path.is_file():
        raise ValueError(f"SQLite dosyasi bulunamadi: {source_path}")
    if source_path == target_path:
        raise ValueError("Kaynak ve hedef ayni olamaz.")
    if os.path.lexists(target_path):
        raise FileExistsError(
            "Hedef zaten var; mevcut veritabani otomatik overwrite edilmez."
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        source_identity = _file_identity(source_path)
        source_db = sqlite3.connect(
            f"{source_path.as_uri()}?mode=ro", uri=True, timeout=10
        )
        destination_db = sqlite3.connect(temporary_path)
        try:
            # Pin verification and backup to one WAL read snapshot.
            source_db.execute("BEGIN")
            source_info = _verify_connection(source_db, source_path)
            destination_db.execute("PRAGMA journal_mode = DELETE")
            destination_db.execute("PRAGMA synchronous = FULL")
            source_db.backup(destination_db)
            integrity = destination_db.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            if integrity != "ok":
                raise ValueError(
                    f"Olusturulan kopya bozuk: {integrity}"
                )
            destination_db.commit()
        finally:
            destination_db.close()
            source_db.close()
        if not _has_identity(source_path, source_identity):
            raise ValueError("Kaynak veritabani backup sirasinda degisti.")
        with temporary_path.open("rb+") as copied:
            copied.flush()
            os.fsync(copied.fileno())
        # Hard-link publication is atomic and fails if another process created
        # the target after our initial existence check. os.replace would
        # silently overwrite that new target in this TOCTOU window.
        temporary_fingerprint = _file_fingerprint(temporary_path)
        os.link(temporary_path, target_path)
        _fsync_directory(target_path.parent)
        try:
            if not _has_fingerprint(target_path, temporary_fingerprint):
                raise ValueError("Yayimlanan backup dosyasi beklenmedik bicimde degisti.")
            result = verify_database(target_path)
            if not _has_fingerprint(target_path, temporary_fingerprint):
                raise ValueError("Backup dogrulama sirasinda degistirildi.")
            if (
                result["schema_version"] != source_info["schema_version"]
                or result["migration_count"] != source_info["migration_count"]
            ):
                raise ValueError("Kopya schema metadata'si kaynakla uyusmuyor.")
        except Exception:
            if (
                os.path.lexists(target_path)
                and _has_fingerprint(target_path, temporary_fingerprint)
            ):
                target_path.unlink()
                _fsync_directory(target_path.parent)
            raise
        temporary_path.unlink()
        temporary_path = None
        _fsync_directory(target_path.parent)
        return result
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tetsu SQLite backup/restore/verify araci."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("backup", "restore"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--source", required=True, type=Path)
        subparser.add_argument("--target", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--source", required=True, type=Path)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.command == "verify":
        result = verify_database(arguments.source)
    else:
        # Restore intentionally has the same safe copy semantics as backup:
        # it only creates a new path. Operators swap paths while API is stopped.
        result = copy_database(arguments.source, arguments.target)
    print(
        f"ok path={result['path']} schema={result['schema_version']} "
        f"bytes={result['byte_size']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
