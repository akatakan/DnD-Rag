from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from api.security import LOCAL_AUTH_PEPPER
from api.store import GameStore


def _plans(
    current: datetime,
    credential_grace_days: int,
    audit_retention_days: int,
) -> list[tuple[str, str, tuple[str, ...]]]:
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Retention zamani timezone-aware olmali.")
    if not 1 <= credential_grace_days <= 3650:
        raise ValueError("Credential grace 1..3650 gun olmali.")
    if not 30 <= audit_retention_days <= 3650:
        raise ValueError("Audit retention 30..3650 gun olmali.")
    now_value = current.astimezone(UTC).isoformat()
    credential_cutoff = (
        current.astimezone(UTC)
        - timedelta(days=credential_grace_days)
    ).isoformat()
    audit_cutoff = (
        current.astimezone(UTC)
        - timedelta(days=audit_retention_days)
    ).isoformat()
    return [
        (
            "websocket_tickets",
            """
            julianday(expires_at) < julianday(?)
            OR (used_at IS NOT NULL
                AND julianday(created_at) < julianday(?))
            """,
            (now_value, credential_cutoff),
        ),
        (
            "map_transients",
            "julianday(expires_at) < julianday(?)",
            (now_value,),
        ),
        (
            "auth_tokens",
            """
            (
                julianday(expires_at) < julianday(?)
                OR (revoked_at IS NOT NULL
                    AND julianday(revoked_at) < julianday(?))
            )
            AND NOT EXISTS (
                SELECT 1 FROM auth_tokens AS child
                WHERE child.rotated_from_id = auth_tokens.id
            )
            """,
            (credential_cutoff, credential_cutoff),
        ),
        (
            "game_invites",
            """
            julianday(expires_at) < julianday(?)
            OR (revoked_at IS NOT NULL
                AND julianday(revoked_at) < julianday(?))
            """,
            (credential_cutoff, credential_cutoff),
        ),
        (
            "command_receipts",
            "julianday(created_at) < julianday(?)",
            (credential_cutoff,),
        ),
        (
            "security_audit_events",
            "julianday(created_at) < julianday(?)",
            (audit_cutoff,),
        ),
    ]


def retention_preview(
    store: GameStore,
    *,
    current: datetime | None = None,
    credential_grace_days: int = 30,
    audit_retention_days: int = 365,
) -> dict[str, int]:
    timestamp = current or datetime.now(UTC)
    counts = {}
    with store.read_transaction():
        with store.connect() as db:
            for table, predicate, parameters in _plans(
                timestamp,
                credential_grace_days,
                audit_retention_days,
            ):
                counts[table] = int(
                    db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {predicate}",
                        parameters,
                    ).fetchone()[0]
                )
    return counts


def apply_retention(
    store: GameStore,
    *,
    confirmation: str,
    current: datetime | None = None,
    credential_grace_days: int = 30,
    audit_retention_days: int = 365,
) -> dict[str, int]:
    if confirmation != "PURGE_EXPIRED_RUNTIME_DATA":
        raise ValueError("Retention confirmation gecersiz.")
    timestamp = current or datetime.now(UTC)
    deleted = {}
    with store.transaction():
        with store.connect() as db:
            for table, predicate, parameters in _plans(
                timestamp,
                credential_grace_days,
                audit_retention_days,
            ):
                cursor = db.execute(
                    f"DELETE FROM {table} WHERE {predicate}",
                    parameters,
                )
                deleted[table] = int(cursor.rowcount)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tetsu expired runtime data retention araci."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("GAME_DB", "runtime/multiplayer.db")),
    )
    parser.add_argument("--credential-grace-days", type=int, default=30)
    parser.add_argument("--audit-retention-days", type=int, default=365)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    arguments = parser.parse_args()
    store = GameStore(
        arguments.database,
        auth_pepper=(
            os.getenv("AUTH_PEPPER", "").strip()
            or LOCAL_AUTH_PEPPER
        ),
    )
    if arguments.apply:
        result = apply_retention(
            store,
            confirmation=arguments.confirm,
            credential_grace_days=arguments.credential_grace_days,
            audit_retention_days=arguments.audit_retention_days,
        )
        mode = "applied"
    else:
        result = retention_preview(
            store,
            credential_grace_days=arguments.credential_grace_days,
            audit_retention_days=arguments.audit_retention_days,
        )
        mode = "dry-run"
    print(json.dumps({"mode": mode, "rows": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
