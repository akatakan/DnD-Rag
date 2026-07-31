import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from api.character_engine import CharacterEngine
from api.character_draft_engine import (
    DRAFT_STEPS,
    CharacterDraftEngine,
    CharacterDraftStorageError,
    CharacterDraftValidationError,
)
from api.encounter_engine import (
    ENCOUNTER_SCHEMA_VERSION,
    EncounterDraftConflict,
    EncounterEngine,
    EncounterStorageError,
    EncounterValidationError,
)
from api.migrations import apply_migrations
from api.models import AuthContext, DMMode
from api.rules_catalog import RulesCatalog


INVITE_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_invite_code() -> str:
    raw = "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(16))
    return f"{raw[:8]}-{raw[8:]}"


def _redact_export_value(value, sensitive_keys: set[str]):
    if isinstance(value, dict):
        return {
            key: _redact_export_value(item, sensitive_keys)
            for key, item in value.items()
            if key.lower() not in sensitive_keys
        }
    if isinstance(value, list):
        return [
            _redact_export_value(item, sensitive_keys) for item in value
        ]
    return value


class MapSceneConflict(ValueError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Map scene revision degisti: {expected} != {actual}."
        )


class MapTokenConflict(ValueError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Map token revision degisti: {expected} != {actual}."
        )


class MapFogConflict(ValueError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Map fog revision degisti: {expected} != {actual}."
        )


def validated_session_summary(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError("Session summary obje olmali.")
    if not raw:
        return {}
    if set(raw) != {
        "schema_version", "title", "highlights", "next_steps", "published"
    }:
        raise RuntimeError("Session summary alanlari gecersiz.")
    if (
        raw.get("schema_version") != 1
        or isinstance(raw.get("schema_version"), bool)
        or not isinstance(raw.get("title"), str)
        or len(raw["title"]) > 160
        or not isinstance(raw.get("published"), bool)
    ):
        raise RuntimeError("Session summary metadata gecersiz.")
    for field in ("highlights", "next_steps"):
        values = raw.get(field)
        if (
            not isinstance(values, list)
            or len(values) > 50
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 500
                for value in values
            )
        ):
            raise RuntimeError(f"Session summary {field} gecersiz.")
    return raw


class GameStore:
    def __init__(
        self,
        path: Path,
        auth_pepper: str = "tetsu-local-development-pepper",
        token_ttl_hours: int = 24 * 30,
        invite_ttl_hours: int = 24 * 7,
        allow_existing_pepper_bind: bool = False,
    ):
        self.path = path
        self.auth_pepper = auth_pepper.encode("utf-8")
        self.token_ttl_hours = token_ttl_hours
        self.invite_ttl_hours = invite_ttl_hours
        self.allow_existing_pepper_bind = allow_existing_pepper_bind
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._configure_database()
        self._initialize()
        self.rules_catalog = RulesCatalog(database_path=self.path)
        self.character_engine = CharacterEngine(self.rules_catalog)
        self.character_draft_engine = CharacterDraftEngine(self.character_engine)
        self.encounter_engine = EncounterEngine()

    def _new_connection(
        self, *, isolation_level: str | None = ""
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=isolation_level,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _configure_database(self) -> None:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            deadline = time.monotonic() + 10
            while True:
                try:
                    mode = connection.execute(
                        "PRAGMA journal_mode = WAL"
                    ).fetchone()[0]
                    break
                except sqlite3.OperationalError as error:
                    if (
                        "locked" not in str(error).lower()
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.05)
            if str(mode).lower() != "wal":
                raise RuntimeError("SQLite WAL modu etkinlestirilemedi.")
            connection.execute("PRAGMA synchronous = FULL")
        finally:
            connection.close()

    @contextmanager
    def connect(self):
        active = getattr(self._local, "connection", None)
        if active is not None:
            yield active
            return
        connection = self._new_connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        """Run nested store calls in one serialized SQLite transaction."""
        active = getattr(self._local, "connection", None)
        if active is not None:
            yield
            return

        connection = self._new_connection(isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        self._local.connection = connection
        try:
            yield
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._local.connection = None
            connection.close()

    @contextmanager
    def read_transaction(self):
        """Read a consistent multi-table snapshot without reserving a writer."""
        active = getattr(self._local, "connection", None)
        if active is not None:
            yield
            return

        connection = self._new_connection(isolation_level=None)
        connection.execute("BEGIN")
        self._local.connection = connection
        try:
            yield
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._local.connection = None
            connection.close()

    def _initialize(self) -> None:
        with self.transaction():
            with self.connect() as db:
                apply_migrations(db)
                self._verify_auth_pepper(db)
                self._migrate_legacy_credentials(db)

    def _credential_hash(self, secret: str, purpose: str) -> str:
        return hmac.new(
            self.auth_pepper,
            f"{purpose}:{secret}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _verify_auth_pepper(self, db: sqlite3.Connection) -> None:
        fingerprint = hashlib.sha256(self.auth_pepper).hexdigest()
        row = db.execute(
            "SELECT pepper_fingerprint FROM auth_configuration WHERE id = 1"
        ).fetchone()
        if row is None:
            credential_count = db.execute(
                """SELECT
                (SELECT COUNT(*) FROM auth_tokens)
                + (SELECT COUNT(*) FROM game_invites)"""
            ).fetchone()[0]
            if credential_count and not self.allow_existing_pepper_bind:
                raise RuntimeError(
                    "Mevcut v5/v6 credential'larını bu AUTH_PEPPER'a bağlamak "
                    "için AUTH_PEPPER_BIND_EXISTING=true ile kontrollü onay gerekir."
                )
            db.execute(
                """INSERT INTO auth_configuration
                (id, pepper_fingerprint, created_at) VALUES (1, ?, ?)""",
                (fingerprint, now()),
            )
        elif not hmac.compare_digest(row["pepper_fingerprint"], fingerprint):
            raise RuntimeError(
                "AUTH_PEPPER mevcut veritabanıyla uyuşmuyor. "
                "Pepper değişimi kontrollü credential reissue gerektirir."
            )

    @staticmethod
    def _expires_at(hours: int) -> str:
        return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()

    def _migrate_legacy_credentials(self, db: sqlite3.Connection) -> None:
        timestamp = now()
        for member in db.execute(
            """SELECT id, token FROM members
            WHERE token NOT LIKE 'hashed:%'"""
        ).fetchall():
            token_hash = self._credential_hash(member["token"], "auth")
            db.execute(
                """INSERT OR IGNORE INTO auth_tokens
                (id, member_id, token_hash, expires_at, revoked_at,
                 rotated_from_id, created_at)
                VALUES (?, ?, ?, ?, NULL, NULL, ?)""",
                (
                    uuid4().hex, member["id"], token_hash,
                    self._expires_at(self.token_ttl_hours), timestamp,
                ),
            )
            db.execute(
                "UPDATE members SET token = ? WHERE id = ?",
                (f"hashed:{token_hash}", member["id"]),
            )
        for game in db.execute(
            """SELECT id, owner_id, invite_code FROM games
            WHERE invite_code NOT LIKE 'hashed:%'"""
        ).fetchall():
            code_hash = self._credential_hash(game["invite_code"].upper(), "invite")
            db.execute(
                """INSERT OR IGNORE INTO game_invites
                (id, game_id, code_hash, expires_at, revoked_at, max_uses,
                 use_count, created_by, created_at)
                VALUES (?, ?, ?, ?, NULL, 50, 0, ?, ?)""",
                (
                    uuid4().hex, game["id"], code_hash,
                    self._expires_at(self.invite_ttl_hours),
                    game["owner_id"], timestamp,
                ),
            )
            db.execute(
                "UPDATE games SET invite_code = ? WHERE id = ?",
                (f"hashed:{code_hash}", game["id"]),
            )

    @staticmethod
    def _add_security_audit(
        db: sqlite3.Connection,
        game_id: str | None,
        actor_id: str | None,
        action: str,
        target_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        db.execute(
            """INSERT INTO security_audit_events
            (game_id, actor_id, action, target_id, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                game_id, actor_id, action, target_id,
                json.dumps(metadata or {}, ensure_ascii=False), now(),
            ),
        )

    @staticmethod
    def _initial_state() -> dict:
        return {
            "round": 0, "turn_index": 0, "turn_serial": 0,
            "turn_actions": {},
            "encounter_status": "idle",
            "active_encounter_id": None,
            "active_encounter_revision": None,
            "combatants": [], "characters": {},
            "scene": {"title": "New Adventure", "description": "", "public_notes": ""},
        }

    def create_game(self, name: str, dm_name: str, dm_mode: DMMode) -> dict:
        with self.transaction():
            return self._create_game(name, dm_name, dm_mode)

    def _create_game(self, name: str, dm_name: str, dm_mode: DMMode) -> dict:
        game_id, member_id, session_id = uuid4().hex, uuid4().hex, uuid4().hex
        invite_code, token, timestamp = (
            new_invite_code(),
            secrets.token_urlsafe(32),
            now(),
        )
        ruleset_version = self.rules_catalog.default_version()
        invite_hash = self._credential_hash(invite_code, "invite")
        token_hash = self._credential_hash(token, "auth")
        token_expires_at = self._expires_at(self.token_ttl_hours)
        invite_expires_at = self._expires_at(self.invite_ttl_hours)
        with self.connect() as db:
            db.execute(
                """INSERT INTO games
                (id, name, invite_code, dm_mode, state_json, created_at, updated_at,
                 owner_id, active_dm_id, fallback_dm_mode, handover_json,
                 campaign_id, active_session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'assisted', '{}', ?, ?)""",
                (game_id, name, f"hashed:{invite_hash}", dm_mode, json.dumps(self._initial_state()), timestamp, timestamp, member_id, member_id, game_id, session_id),
            )
            db.execute(
                """INSERT INTO members
                (id, game_id, name, role, character_id, token, created_at)
                VALUES (?, ?, ?, 'dm', NULL, ?, ?)""",
                (member_id, game_id, dm_name, f"hashed:{token_hash}", timestamp),
            )
            db.execute(
                """INSERT INTO campaigns
                (id, name, owner_id, status, ruleset_version, language, play_style,
                 public_notes, settings_json, settings_version, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, 'tr', 'theater',
                        '', ?, 1, ?, ?)""",
                (
                    game_id,
                    name,
                    member_id,
                    ruleset_version,
                    json.dumps({
                        "schema_version": 1,
                        "house_rules": [],
                        "safety_tools": [
                            "x_card", "lines_veils", "open_door"
                        ],
                        "session_zero_agenda": [],
                    }),
                    timestamp,
                    timestamp,
                ),
            )
            db.execute(
                """INSERT INTO sessions
                (id, campaign_id, number, title, status, scheduled_at, started_at,
                 ended_at, summary_json, created_at, updated_at)
                VALUES (?, ?, 1, 'Session 1', 'preparing', NULL, NULL,
                        NULL, '{}', ?, ?)""",
                (session_id, game_id, timestamp, timestamp),
            )
            db.execute(
                """INSERT INTO campaign_members
                (campaign_id, member_id, readiness_status, joined_at,
                 readiness_version, consent_status,
                 safety_preferences_json, updated_at)
                VALUES (?, ?, 'not_ready', ?, 1, 'pending', '{}', ?)""",
                (game_id, member_id, timestamp, timestamp),
            )
            db.execute(
                """INSERT INTO auth_tokens
                (id, member_id, token_hash, expires_at, revoked_at,
                 rotated_from_id, created_at)
                VALUES (?, ?, ?, ?, NULL, NULL, ?)""",
                (
                    uuid4().hex, member_id, token_hash,
                    token_expires_at, timestamp,
                ),
            )
            invite_id = uuid4().hex
            db.execute(
                """INSERT INTO game_invites
                (id, game_id, code_hash, expires_at, revoked_at, max_uses,
                 use_count, created_by, created_at)
                VALUES (?, ?, ?, ?, NULL, 50, 0, ?, ?)""",
                (
                    invite_id, game_id, invite_hash, invite_expires_at,
                    member_id, timestamp,
                ),
            )
            self._add_security_audit(
                db, game_id, member_id, "game_created", game_id
            )
            db.execute(
                """
                INSERT INTO map_scenes (
                    game_id, campaign_id, asset_id, name, grid_type,
                    grid_size_px, distance_per_cell, distance_unit,
                    viewport_x, viewport_y, viewport_zoom, published,
                    revision, updated_at
                ) VALUES (?, ?, NULL, 'Battle Map', 'square', 70, 5, 'ft',
                    0, 0, 1, 0, 1, ?)
                """,
                (game_id, game_id, timestamp),
            )
            db.execute(
                """
                INSERT INTO map_fog_state (
                    game_id, campaign_id, enabled, revision, updated_at
                ) VALUES (?, ?, 0, 1, ?)
                """,
                (game_id, game_id, timestamp),
            )
        self.add_event(game_id, "game_created", member_id, "public", {"name": name, "dm_mode": dm_mode})
        return {
            "game_id": game_id, "campaign_id": game_id, "session_id": session_id,
            "member_id": member_id, "invite_code": invite_code, "token": token,
            "role": "dm", "token_expires_at": token_expires_at,
            "invite_expires_at": invite_expires_at,
        }

    def join_game(self, invite_code: str, player_name: str) -> dict:
        with self.transaction():
            return self._join_game(invite_code, player_name)

    def _join_game(self, invite_code: str, player_name: str) -> dict:
        member_id, character_id, token, timestamp = uuid4().hex, uuid4().hex, secrets.token_urlsafe(32), now()
        invite_hash = self._credential_hash(
            invite_code.strip().upper(), "invite"
        )
        token_hash = self._credential_hash(token, "auth")
        token_expires_at = self._expires_at(self.token_ttl_hours)
        with self.connect() as db:
            game = db.execute(
                """SELECT game_invites.id AS invite_id, games.id,
                games.state_json, games.campaign_id, games.active_session_id,
                games.state_revision, campaigns.ruleset_version
                FROM game_invites
                JOIN games ON games.id = game_invites.game_id
                JOIN campaigns ON campaigns.id = games.campaign_id
                WHERE game_invites.code_hash = ?
                AND game_invites.revoked_at IS NULL
                AND game_invites.expires_at > ?
                AND game_invites.use_count < game_invites.max_uses""",
                (invite_hash, timestamp),
            ).fetchone()
            if game is None:
                raise KeyError("Davet kodu bulunamadı.")
            state = json.loads(game["state_json"])
            character = self.character_engine.new_character(
                character_id,
                member_id,
                player_name,
                ruleset_version=game["ruleset_version"],
            )
            state["characters"][character_id] = character
            db.execute(
                """INSERT INTO members
                (id, game_id, name, role, character_id, token, created_at,
                 character_ready)
                VALUES (?, ?, ?, 'player', ?, ?, ?, 0)""",
                (member_id, game["id"], player_name, character_id, f"hashed:{token_hash}", timestamp),
            )
            self.create_character_draft(
                game["id"], character, initial_creation=True
            )
            db.execute(
                """INSERT INTO campaign_members
                (campaign_id, member_id, readiness_status, joined_at,
                 readiness_version, consent_status,
                 safety_preferences_json, updated_at)
                VALUES (?, ?, 'not_ready', ?, 1, 'pending', '{}', ?)""",
                (game["campaign_id"], member_id, timestamp, timestamp),
            )
            db.execute("UPDATE games SET state_json = ?, updated_at = ? WHERE id = ?", (json.dumps(state), timestamp, game["id"]))
            revision = self.advance_revision(
                game["id"], int(game["state_revision"])
            )
            db.execute(
                "UPDATE game_invites SET use_count = use_count + 1 WHERE id = ?",
                (game["invite_id"],),
            )
            db.execute(
                """INSERT INTO auth_tokens
                (id, member_id, token_hash, expires_at, revoked_at,
                 rotated_from_id, created_at)
                VALUES (?, ?, ?, ?, NULL, NULL, ?)""",
                (
                    uuid4().hex, member_id, token_hash,
                    token_expires_at, timestamp,
                ),
            )
            self._add_security_audit(
                db, game["id"], member_id, "invite_used", game["invite_id"]
            )
        self.add_event(game["id"], "player_joined", member_id, "public", {"name": player_name, "character_id": character_id})
        return {
            "game_id": game["id"], "campaign_id": game["campaign_id"],
            "session_id": game["active_session_id"], "member_id": member_id,
            "character_id": character_id, "token": token, "role": "player",
            "revision": revision, "token_expires_at": token_expires_at,
        }

    def authenticate(self, token: str) -> AuthContext | None:
        if not token:
            return None
        token_hash = self._credential_hash(token, "auth")
        with self.connect() as db:
            row = db.execute(
                """SELECT members.game_id, members.id, members.role, members.character_id,
                games.owner_id, auth_tokens.id AS auth_token_id,
                auth_tokens.expires_at AS auth_expires_at FROM auth_tokens
                JOIN members ON members.id = auth_tokens.member_id
                JOIN games ON games.id = members.game_id
                WHERE auth_tokens.token_hash = ?
                AND auth_tokens.revoked_at IS NULL
                AND auth_tokens.expires_at > ?""",
                (token_hash, now()),
            ).fetchone()
        if row is None:
            return None
        return AuthContext(
            game_id=row["game_id"], member_id=row["id"], role=row["role"],
            character_id=row["character_id"], is_owner=row["owner_id"] == row["id"],
            auth_token_id=row["auth_token_id"],
            auth_expires_at=row["auth_expires_at"],
        )

    def refresh_auth_context(self, auth: AuthContext) -> AuthContext | None:
        if not auth.auth_token_id:
            return None
        with self.connect() as db:
            row = db.execute(
                """SELECT members.game_id, members.id, members.role,
                members.character_id, games.owner_id,
                auth_tokens.id AS auth_token_id,
                auth_tokens.expires_at AS auth_expires_at
                FROM auth_tokens
                JOIN members ON members.id = auth_tokens.member_id
                JOIN games ON games.id = members.game_id
                WHERE auth_tokens.id = ? AND members.id = ?
                AND auth_tokens.revoked_at IS NULL
                AND auth_tokens.expires_at > ?""",
                (auth.auth_token_id, auth.member_id, now()),
            ).fetchone()
        if row is None:
            return None
        return AuthContext(
            game_id=row["game_id"], member_id=row["id"], role=row["role"],
            character_id=row["character_id"],
            is_owner=row["owner_id"] == row["id"],
            auth_token_id=row["auth_token_id"],
            auth_expires_at=row["auth_expires_at"],
        )

    def auth_context_active(self, auth: AuthContext) -> bool:
        return self.refresh_auth_context(auth) is not None

    def rotate_token(self, token: str) -> dict:
        token_hash = self._credential_hash(token, "auth")
        with self.transaction():
            with self.connect() as db:
                current = db.execute(
                    """SELECT auth_tokens.id, auth_tokens.member_id,
                    members.game_id FROM auth_tokens
                    JOIN members ON members.id = auth_tokens.member_id
                    WHERE auth_tokens.token_hash = ?
                    AND auth_tokens.revoked_at IS NULL
                    AND auth_tokens.expires_at > ?""",
                    (token_hash, now()),
                ).fetchone()
                if current is None:
                    raise KeyError("Aktif oturum token'i bulunamadı.")
                timestamp = now()
                cursor = db.execute(
                    """UPDATE auth_tokens SET revoked_at = ?
                    WHERE id = ? AND revoked_at IS NULL""",
                    (timestamp, current["id"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Token daha önce döndürülmüş.")
                new_token = secrets.token_urlsafe(32)
                new_hash = self._credential_hash(new_token, "auth")
                expires_at = self._expires_at(self.token_ttl_hours)
                new_id = uuid4().hex
                db.execute(
                    """INSERT INTO auth_tokens
                    (id, member_id, token_hash, expires_at, revoked_at,
                     rotated_from_id, created_at)
                    VALUES (?, ?, ?, ?, NULL, ?, ?)""",
                    (
                        new_id, current["member_id"], new_hash, expires_at,
                        current["id"], timestamp,
                    ),
                )
                self._add_security_audit(
                    db, current["game_id"], current["member_id"],
                    "token_rotated", new_id,
                )
        return {"token": new_token, "token_expires_at": expires_at}

    @staticmethod
    def _validate_campaign_vault_secret(secret: str) -> None:
        if (
            not isinstance(secret, str)
            or len(secret) != 64
            or any(character not in "0123456789abcdef" for character in secret)
        ):
            raise ValueError("Campaign vault kimligi gecersiz.")

    def attach_campaign_vault(
        self, auth: AuthContext, secret: str
    ) -> dict:
        self._validate_campaign_vault_secret(secret)
        if auth.role not in {"dm", "co_dm"}:
            raise PermissionError(
                "Campaign vault yalnizca DM rolleri icindir."
            )
        secret_hash = self._credential_hash(secret, "campaign_vault")
        timestamp = now()
        expires_at = self._expires_at(24 * 365)
        with self.transaction():
            with self.connect() as db:
                scope = db.execute(
                    """SELECT games.campaign_id
                    FROM games
                    JOIN members ON members.game_id = games.id
                    WHERE games.id = ? AND members.id = ?""",
                    (auth.game_id, auth.member_id),
                ).fetchone()
                if scope is None:
                    raise ValueError("Campaign uyeligi bulunamadi.")
                vault = db.execute(
                    """SELECT id FROM campaign_device_vaults
                    WHERE secret_hash = ?""",
                    (secret_hash,),
                ).fetchone()
                if vault is None:
                    vault_id = uuid4().hex
                    db.execute(
                        """INSERT INTO campaign_device_vaults (
                            id, secret_hash, expires_at, last_seen_at, created_at
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            vault_id, secret_hash, expires_at,
                            timestamp, timestamp,
                        ),
                    )
                else:
                    vault_id = vault["id"]
                    db.execute(
                        """UPDATE campaign_device_vaults
                        SET expires_at = ?, last_seen_at = ?
                        WHERE id = ?""",
                        (expires_at, timestamp, vault_id),
                    )
                db.execute(
                    """INSERT INTO campaign_device_memberships (
                        vault_id, campaign_id, member_id, created_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(vault_id, campaign_id) DO UPDATE SET
                        member_id = excluded.member_id""",
                    (
                        vault_id, scope["campaign_id"],
                        auth.member_id, timestamp,
                    ),
                )
                self._add_security_audit(
                    db, auth.game_id, auth.member_id,
                    "campaign_vault_attached", vault_id,
                )
        return {"attached": True, "expires_at": expires_at}

    def campaign_vault_campaigns(self, secret: str) -> list[dict]:
        self._validate_campaign_vault_secret(secret)
        secret_hash = self._credential_hash(secret, "campaign_vault")
        timestamp = now()
        with self.transaction():
            with self.connect() as db:
                vault = db.execute(
                    """SELECT id FROM campaign_device_vaults
                    WHERE secret_hash = ? AND expires_at > ?""",
                    (secret_hash, timestamp),
                ).fetchone()
                if vault is None:
                    return []
                db.execute(
                    """UPDATE campaign_device_vaults SET last_seen_at = ?
                    WHERE id = ?""",
                    (timestamp, vault["id"]),
                )
                rows = db.execute(
                    """SELECT
                        games.id AS game_id,
                        games.campaign_id,
                        games.active_session_id AS session_id,
                        games.updated_at,
                        campaigns.name,
                        campaigns.status,
                        members.role,
                        CASE WHEN games.owner_id = members.id
                            THEN 1 ELSE 0 END AS is_owner
                    FROM campaign_device_memberships AS links
                    JOIN members ON members.id = links.member_id
                    JOIN games ON games.id = members.game_id
                      AND games.campaign_id = links.campaign_id
                    JOIN campaigns ON campaigns.id = links.campaign_id
                    WHERE links.vault_id = ?
                      AND members.role IN ('dm', 'co_dm')
                    ORDER BY games.updated_at DESC, campaigns.name""",
                    (vault["id"],),
                ).fetchall()
        return [
            {
                "game_id": row["game_id"],
                "campaign_id": row["campaign_id"],
                "session_id": row["session_id"],
                "name": row["name"],
                "status": row["status"],
                "role": row["role"],
                "is_owner": bool(row["is_owner"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def resume_campaign_vault(self, secret: str, game_id: str) -> dict:
        self._validate_campaign_vault_secret(secret)
        secret_hash = self._credential_hash(secret, "campaign_vault")
        timestamp = now()
        with self.transaction():
            with self.connect() as db:
                member = db.execute(
                    """SELECT
                        members.id AS member_id,
                        members.role,
                        members.character_id,
                        games.id AS game_id,
                        games.campaign_id,
                        games.active_session_id AS session_id,
                        campaign_device_vaults.id AS vault_id
                    FROM campaign_device_vaults
                    JOIN campaign_device_memberships AS links
                      ON links.vault_id = campaign_device_vaults.id
                    JOIN members ON members.id = links.member_id
                    JOIN games ON games.id = members.game_id
                    WHERE campaign_device_vaults.secret_hash = ?
                      AND campaign_device_vaults.expires_at > ?
                      AND games.id = ?""",
                    (secret_hash, timestamp, game_id),
                ).fetchone()
                if member is None or member["role"] not in {"dm", "co_dm"}:
                    raise KeyError("Campaign bu cihaz kasasinda bulunamadi.")
                token = secrets.token_urlsafe(32)
                token_hash = self._credential_hash(token, "auth")
                expires_at = self._expires_at(self.token_ttl_hours)
                token_id = uuid4().hex
                db.execute(
                    """INSERT INTO auth_tokens (
                        id, member_id, token_hash, expires_at, revoked_at,
                        rotated_from_id, created_at
                    ) VALUES (?, ?, ?, ?, NULL, NULL, ?)""",
                    (
                        token_id, member["member_id"], token_hash,
                        expires_at, timestamp,
                    ),
                )
                db.execute(
                    "UPDATE members SET token = ? WHERE id = ?",
                    (
                        f"hashed:{token_hash}",
                        member["member_id"],
                    ),
                )
                db.execute(
                    """UPDATE campaign_device_vaults
                    SET last_seen_at = ? WHERE id = ?""",
                    (timestamp, member["vault_id"]),
                )
                self._add_security_audit(
                    db, member["game_id"], member["member_id"],
                    "campaign_vault_resumed", token_id,
                )
        return {
            "game_id": member["game_id"],
            "campaign_id": member["campaign_id"],
            "session_id": member["session_id"],
            "member_id": member["member_id"],
            "character_id": member["character_id"],
            "role": member["role"],
            "token": token,
            "token_expires_at": expires_at,
        }

    def detach_campaign_vault(
        self, secret: str, game_id: str
    ) -> bool:
        self._validate_campaign_vault_secret(secret)
        secret_hash = self._credential_hash(secret, "campaign_vault")
        with self.transaction():
            with self.connect() as db:
                cursor = db.execute(
                    """DELETE FROM campaign_device_memberships
                    WHERE campaign_id = (
                        SELECT campaign_id FROM games WHERE id = ?
                    )
                    AND vault_id = (
                        SELECT id FROM campaign_device_vaults
                        WHERE secret_hash = ?
                    )""",
                    (game_id, secret_hash),
                )
        return cursor.rowcount == 1

    def revoke_token(self, token: str) -> bool:
        token_hash = self._credential_hash(token, "auth")
        with self.transaction():
            with self.connect() as db:
                current = db.execute(
                    """SELECT auth_tokens.id, auth_tokens.member_id,
                    members.game_id FROM auth_tokens
                    JOIN members ON members.id = auth_tokens.member_id
                    WHERE auth_tokens.token_hash = ?
                    AND auth_tokens.revoked_at IS NULL""",
                    (token_hash,),
                ).fetchone()
                if current is None:
                    return False
                db.execute(
                    "UPDATE auth_tokens SET revoked_at = ? WHERE id = ?",
                    (now(), current["id"]),
                )
                self._add_security_audit(
                    db, current["game_id"], current["member_id"],
                    "token_revoked", current["id"],
                )
        return True

    def rotate_invite(
        self, game_id: str, actor_id: str, max_uses: int = 50
    ) -> dict:
        max_uses = min(500, max(1, int(max_uses)))
        code = new_invite_code()
        code_hash = self._credential_hash(code, "invite")
        timestamp = now()
        expires_at = self._expires_at(self.invite_ttl_hours)
        invite_id = uuid4().hex
        with self.transaction():
            with self.connect() as db:
                db.execute(
                    """UPDATE game_invites SET revoked_at = ?
                    WHERE game_id = ? AND revoked_at IS NULL""",
                    (timestamp, game_id),
                )
                db.execute(
                    """INSERT INTO game_invites
                    (id, game_id, code_hash, expires_at, revoked_at, max_uses,
                     use_count, created_by, created_at)
                    VALUES (?, ?, ?, ?, NULL, ?, 0, ?, ?)""",
                    (
                        invite_id, game_id, code_hash, expires_at,
                        max_uses, actor_id, timestamp,
                    ),
                )
                db.execute(
                    "UPDATE games SET invite_code = ?, updated_at = ? WHERE id = ?",
                    (f"hashed:{code_hash}", timestamp, game_id),
                )
                self._add_security_audit(
                    db, game_id, actor_id, "invite_rotated", invite_id,
                    {"max_uses": max_uses, "expires_at": expires_at},
                )
        return {
            "invite_code": code, "invite_id": invite_id,
            "expires_at": expires_at, "max_uses": max_uses, "use_count": 0,
        }

    def revoke_invites(self, game_id: str, actor_id: str) -> int:
        with self.transaction():
            with self.connect() as db:
                timestamp = now()
                cursor = db.execute(
                    """UPDATE game_invites SET revoked_at = ?
                    WHERE game_id = ? AND revoked_at IS NULL""",
                    (timestamp, game_id),
                )
                self._add_security_audit(
                    db, game_id, actor_id, "invites_revoked", game_id,
                    {"count": cursor.rowcount},
                )
                return cursor.rowcount

    def active_invite(self, game_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT id, expires_at, max_uses, use_count, created_at
                FROM game_invites WHERE game_id = ? AND revoked_at IS NULL
                AND expires_at > ? AND use_count < max_uses
                ORDER BY created_at DESC LIMIT 1""",
                (game_id, now()),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_websocket_ticket(self, auth: AuthContext, token: str) -> dict:
        ticket = secrets.token_urlsafe(32)
        ticket_hash = self._credential_hash(ticket, "websocket")
        token_hash = self._credential_hash(token, "auth")
        timestamp = now()
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=60)
        ).isoformat()
        with self.connect() as db:
            db.execute(
                """DELETE FROM websocket_tickets
                WHERE expires_at <= ? OR used_at IS NOT NULL""",
                (timestamp,),
            )
            auth_token = db.execute(
                """SELECT id FROM auth_tokens WHERE member_id = ?
                AND token_hash = ? AND revoked_at IS NULL AND expires_at > ?""",
                (auth.member_id, token_hash, timestamp),
            ).fetchone()
            if auth_token is None:
                raise KeyError("Aktif auth token bulunamadı.")
            db.execute(
                """INSERT INTO websocket_tickets
                (id, member_id, ticket_hash, expires_at, used_at, created_at,
                 auth_token_id)
                VALUES (?, ?, ?, ?, NULL, ?, ?)""",
                (
                    uuid4().hex, auth.member_id, ticket_hash, expires_at,
                    timestamp, auth_token["id"],
                ),
            )
        return {"ticket": ticket, "expires_at": expires_at}

    def consume_websocket_ticket(
        self, ticket: str, game_id: str
    ) -> AuthContext | None:
        ticket_hash = self._credential_hash(ticket, "websocket")
        with self.transaction():
            with self.connect() as db:
                row = db.execute(
                    """SELECT websocket_tickets.id AS ticket_id,
                    members.game_id, members.id, members.role,
                    members.character_id, games.owner_id,
                    auth_tokens.id AS auth_token_id,
                    auth_tokens.expires_at AS auth_expires_at
                    FROM websocket_tickets
                    JOIN members ON members.id = websocket_tickets.member_id
                    JOIN games ON games.id = members.game_id
                    JOIN auth_tokens
                        ON auth_tokens.id = websocket_tickets.auth_token_id
                    WHERE websocket_tickets.ticket_hash = ?
                    AND websocket_tickets.used_at IS NULL
                    AND websocket_tickets.expires_at > ?
                    AND auth_tokens.revoked_at IS NULL
                    AND auth_tokens.expires_at > ?
                    AND members.game_id = ?""",
                    (ticket_hash, now(), now(), game_id),
                ).fetchone()
                if row is None:
                    return None
                cursor = db.execute(
                    """UPDATE websocket_tickets SET used_at = ?
                    WHERE id = ? AND used_at IS NULL""",
                    (now(), row["ticket_id"]),
                )
                if cursor.rowcount != 1:
                    return None
        return AuthContext(
            game_id=row["game_id"], member_id=row["id"], role=row["role"],
            character_id=row["character_id"],
            is_owner=row["owner_id"] == row["id"],
            auth_token_id=row["auth_token_id"],
            auth_expires_at=row["auth_expires_at"],
        )

    def security_audit(self, game_id: str, limit: int = 100) -> list[dict]:
        limit = min(500, max(1, int(limit)))
        with self.connect() as db:
            rows = db.execute(
                """SELECT id, actor_id, action, target_id, metadata_json,
                created_at FROM security_audit_events
                WHERE game_id = ? ORDER BY id DESC LIMIT ?""",
                (game_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"], "actor_id": row["actor_id"],
                "action": row["action"], "target_id": row["target_id"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def game(self, game_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if row is None:
            raise KeyError("Oyun bulunamadı.")
        result = dict(row)
        result["state"] = json.loads(result.pop("state_json"))
        result["handover"] = json.loads(result.pop("handover_json") or "{}")
        return result

    def save_state(self, game_id: str, state: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE games SET state_json = ?, updated_at = ? WHERE id = ?", (json.dumps(state, ensure_ascii=False), now(), game_id))

    def advance_revision(self, game_id: str, expected_revision: int) -> int:
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE games SET state_revision = state_revision + 1,
                updated_at = ? WHERE id = ? AND state_revision = ?""",
                (now(), game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("Oyun revision'i işlem sırasında değişti.")
        return expected_revision + 1

    def command_receipt(
        self, game_id: str, actor_id: str, client_action_id: str
    ) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT command_type, request_hash, response_json, created_at
                FROM command_receipts
                WHERE game_id = ? AND actor_id = ? AND client_action_id = ?""",
                (game_id, actor_id, client_action_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "command_type": row["command_type"],
            "request_hash": row["request_hash"],
            "response": json.loads(row["response_json"]),
            "created_at": row["created_at"],
        }

    def save_command_receipt(
        self,
        game_id: str,
        actor_id: str,
        client_action_id: str,
        command_type: str,
        request_hash: str,
        response: dict,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO command_receipts
                (game_id, actor_id, client_action_id, command_type, request_hash,
                 response_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    game_id, actor_id, client_action_id, command_type, request_hash,
                    json.dumps(response, ensure_ascii=False), now(),
                ),
            )

    def members(self, game_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id, name, role, character_id FROM members WHERE game_id = ? ORDER BY created_at", (game_id,)).fetchall()
        return [dict(row) for row in rows]

    def member(self, game_id: str, member_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT id, name, role, character_id FROM members WHERE game_id = ? AND id = ?", (game_id, member_id)).fetchone()
        if row is None:
            raise KeyError("Üye bulunamadı.")
        return dict(row)

    def character_creation_required(
        self, game_id: str, member_id: str
    ) -> bool:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT role, character_id, character_ready
                FROM members
                WHERE game_id = ? AND id = ?
                """,
                (game_id, member_id),
            ).fetchone()
        if row is None:
            raise KeyError("Uye bulunamadi.")
        return (
            row["role"] == "player"
            and row["character_id"] is not None
            and row["character_ready"] == 0
        )

    def _draft_result(self, row: sqlite3.Row) -> dict:
        try:
            data = json.loads(row["draft_json"])
            self.character_draft_engine.validate_shape(data)
        except (json.JSONDecodeError, CharacterDraftValidationError) as error:
            raise CharacterDraftStorageError(
                "Persisted character draft gecersiz."
            ) from error
        if (
            row["schema_version"] != data["schema_version"]
            or row["current_step"] not in DRAFT_STEPS
            or not isinstance(row["revision"], int)
            or row["revision"] < 1
            or row["status"] not in {"active", "published"}
        ):
            raise CharacterDraftStorageError(
                "Persisted character draft metadata gecersiz."
            )
        return {
            "game_id": row["game_id"],
            "character_id": row["character_id"],
            "owner_id": row["owner_id"],
            "schema_version": row["schema_version"],
            "data": data,
            "current_step": row["current_step"],
            "revision": row["revision"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "published_at": row["published_at"],
        }

    def character_draft(self, game_id: str, character_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM character_drafts
                WHERE game_id = ? AND character_id = ?
                """,
                (game_id, character_id),
            ).fetchone()
        return self._draft_result(row) if row is not None else None

    def create_character_draft(
        self,
        game_id: str,
        character: dict,
        *,
        initial_creation: bool = False,
    ) -> dict:
        existing = self.character_draft(game_id, character["id"])
        if existing is not None:
            if existing["status"] == "active":
                reconciled = self.character_draft_engine.reconcile_spellcasting(
                    existing["data"], character["ruleset_version"]
                )
                if reconciled != existing["data"]:
                    return self.update_character_draft(
                        game_id,
                        character["id"],
                        existing["revision"],
                        reconciled,
                        existing["current_step"],
                    )
            return existing
        timestamp = now()
        data = (
            self.character_draft_engine.new_creation_draft(character)
            if initial_creation
            else self.character_draft_engine.from_character(character)
        )
        with self.connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO character_drafts (
                    game_id, character_id, owner_id, schema_version,
                    draft_json, current_step, revision, status,
                    created_at, updated_at, published_at
                ) VALUES (?, ?, ?, ?, ?, 'basics', 1, 'active', ?, ?, NULL)
                """,
                (
                    game_id,
                    character["id"],
                    character["owner_id"],
                    data["schema_version"],
                    json.dumps(data, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        return self.character_draft(game_id, character["id"])

    def mark_character_ready(
        self, game_id: str, character_id: str
    ) -> None:
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE members
                SET character_ready = 1
                WHERE game_id = ? AND character_id = ? AND role = 'player'
                """,
                (game_id, character_id),
            )
            if cursor.rowcount != 1:
                raise CharacterDraftStorageError(
                    "Character owner member kaydi bulunamadi."
                )

    def update_character_draft(
        self,
        game_id: str,
        character_id: str,
        expected_revision: int,
        data: dict,
        current_step: str,
    ) -> dict:
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE character_drafts
                SET draft_json = ?, schema_version = ?, current_step = ?,
                    revision = revision + 1, status = 'active',
                    updated_at = ?, published_at = NULL
                WHERE game_id = ? AND character_id = ? AND revision = ?
                AND status = 'active'
                """,
                (
                    json.dumps(data, ensure_ascii=False),
                    data["schema_version"],
                    current_step,
                    timestamp,
                    game_id,
                    character_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                current = db.execute(
                    """
                    SELECT revision, status FROM character_drafts
                    WHERE game_id = ? AND character_id = ?
                    """,
                    (game_id, character_id),
                ).fetchone()
                if current is None:
                    raise KeyError("Character draft bulunamadi.")
                raise ValueError(
                    f"Draft revision conflict: expected {expected_revision}, "
                    f"actual {current['revision']}; status {current['status']}."
                )
        return self.character_draft(game_id, character_id)

    def mark_character_draft_published(
        self, game_id: str, character_id: str, expected_revision: int
    ) -> dict:
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE character_drafts
                SET status = 'published', revision = revision + 1,
                    updated_at = ?, published_at = ?
                WHERE game_id = ? AND character_id = ?
                AND revision = ? AND status = 'active'
                """,
                (
                    timestamp,
                    timestamp,
                    game_id,
                    character_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Draft publish revision/status conflict.")
        return self.character_draft(game_id, character_id)

    def _encounter_draft_result(self, row: sqlite3.Row) -> dict:
        try:
            data = json.loads(row["draft_json"])
            self.encounter_engine.validate(data)
        except (TypeError, json.JSONDecodeError, EncounterValidationError) as error:
            raise EncounterStorageError(
                "Persisted encounter draft gecersiz."
            ) from error
        if (
            row["schema_version"] != ENCOUNTER_SCHEMA_VERSION
            or row["schema_version"] != data["schema_version"]
            or row["name"] != data["name"]
            or row["description"] != data["description"]
            or not isinstance(row["revision"], int)
            or row["revision"] < 1
        ):
            raise EncounterStorageError(
                "Persisted encounter draft metadata gecersiz."
            )
        return {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "created_by": row["created_by"],
            "schema_version": row["schema_version"],
            "data": data,
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def encounter_drafts(self, campaign_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM encounter_drafts
                WHERE campaign_id = ?
                ORDER BY updated_at DESC, id
                LIMIT 200
                """,
                (campaign_id,),
            ).fetchall()
        return [self._encounter_draft_result(row) for row in rows]

    def encounter_draft(
        self, campaign_id: str, encounter_id: str
    ) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM encounter_drafts
                WHERE campaign_id = ? AND id = ?
                """,
                (campaign_id, encounter_id),
            ).fetchone()
        return self._encounter_draft_result(row) if row is not None else None

    def create_encounter_draft(
        self,
        campaign_id: str,
        actor_id: str,
        name: str,
        description: str = "",
        data: dict | None = None,
    ) -> dict:
        draft = (
            self.encounter_engine.create(name, description)
            if data is None
            else data
        )
        self.encounter_engine.validate(draft)
        encounter_id, timestamp = uuid4().hex, now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO encounter_drafts (
                    id, campaign_id, created_by, schema_version,
                    name, description, draft_json, revision,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    encounter_id, campaign_id, actor_id,
                    ENCOUNTER_SCHEMA_VERSION, draft["name"],
                    draft["description"],
                    json.dumps(draft, ensure_ascii=False),
                    timestamp, timestamp,
                ),
            )
        return self.encounter_draft(campaign_id, encounter_id)

    def update_encounter_draft(
        self,
        campaign_id: str,
        encounter_id: str,
        expected_revision: int,
        data: dict,
    ) -> dict:
        self.encounter_engine.validate(data)
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE encounter_drafts
                SET name = ?, description = ?, draft_json = ?,
                    revision = revision + 1, updated_at = ?
                WHERE campaign_id = ? AND id = ? AND revision = ?
                """,
                (
                    data["name"], data["description"],
                    json.dumps(data, ensure_ascii=False), now(),
                    campaign_id, encounter_id, expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                current = db.execute(
                    """
                    SELECT revision FROM encounter_drafts
                    WHERE campaign_id = ? AND id = ?
                    """,
                    (campaign_id, encounter_id),
                ).fetchone()
                if current is None:
                    raise KeyError("Encounter draft bulunamadi.")
                raise EncounterDraftConflict(
                    expected_revision, int(current["revision"])
                )
        return self.encounter_draft(campaign_id, encounter_id)

    def duplicate_encounter_draft(
        self, campaign_id: str, encounter_id: str, actor_id: str
    ) -> dict:
        source = self.encounter_draft(campaign_id, encounter_id)
        if source is None:
            raise KeyError("Encounter draft bulunamadi.")
        data = json.loads(json.dumps(source["data"], ensure_ascii=False))
        suffix = " (Copy)"
        data["name"] = f"{data['name'][:120 - len(suffix)]}{suffix}"
        return self.create_encounter_draft(
            campaign_id, actor_id, data["name"], data["description"], data
        )

    def push_encounter_undo(
        self,
        game_id: str,
        actor_id: str,
        command_type: str,
        state: dict,
    ) -> None:
        if not isinstance(state, dict):
            raise ValueError("Encounter undo state obje olmali.")
        encoded_state = json.dumps(state, ensure_ascii=False)
        if len(encoded_state.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("Encounter undo state boyut limiti asildi.")
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO encounter_undo_history (
                    game_id, actor_id, command_type, state_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    game_id, actor_id, command_type,
                    encoded_state, now(),
                ),
            )
            db.execute(
                """
                DELETE FROM encounter_undo_history
                WHERE game_id = ? AND id NOT IN (
                    SELECT id FROM encounter_undo_history
                    WHERE game_id = ? ORDER BY id DESC LIMIT 20
                )
                """,
                (game_id, game_id),
            )

    def pop_encounter_undo(self, game_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT id, command_type, created_at,
                       length(CAST(state_json AS BLOB)) AS state_size
                FROM encounter_undo_history
                WHERE game_id = ? ORDER BY id DESC LIMIT 1
                """,
                (game_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Geri alinabilecek encounter islemi yok.")
            if int(row["state_size"]) > 16 * 1024 * 1024:
                raise EncounterStorageError(
                    "Persisted encounter undo state boyut limiti asildi."
                )
            state_row = db.execute(
                """
                SELECT state_json FROM encounter_undo_history
                WHERE game_id = ? AND id = ?
                """,
                (game_id, row["id"]),
            ).fetchone()
            try:
                state = json.loads(state_row["state_json"])
            except (TypeError, json.JSONDecodeError) as error:
                raise EncounterStorageError(
                    "Persisted encounter undo state gecersiz."
                ) from error
            if (
                not isinstance(state, dict)
                or not isinstance(state.get("combatants"), list)
                or len(state["combatants"]) > 200
                or not isinstance(state.get("characters"), dict)
                or not isinstance(state.get("turn_actions"), dict)
                or state.get("encounter_status")
                not in {"idle", "active", "paused", "completed"}
            ):
                raise EncounterStorageError(
                    "Persisted encounter undo state gecersiz."
                )
            seen: set[str] = set()
            for combatant in state["combatants"]:
                if (
                    not isinstance(combatant, dict)
                    or not isinstance(combatant.get("id"), str)
                    or not 1 <= len(combatant["id"]) <= 64
                    or combatant["id"] in seen
                    or isinstance(combatant.get("initiative"), bool)
                    or not isinstance(combatant.get("initiative"), int)
                    or not -100 <= combatant["initiative"] <= 100
                    or isinstance(combatant.get("tie_breaker", 0), bool)
                    or not isinstance(combatant.get("tie_breaker", 0), int)
                    or not -100 <= combatant.get("tie_breaker", 0) <= 100
                ):
                    raise EncounterStorageError(
                        "Persisted encounter undo state gecersiz."
                    )
                seen.add(combatant["id"])
            turn_index = state.get("turn_index")
            if (
                isinstance(turn_index, bool)
                or not isinstance(turn_index, int)
                or (
                    state["encounter_status"] in {"active", "paused"}
                    and not 0 <= turn_index < len(state["combatants"])
                )
            ):
                raise EncounterStorageError(
                    "Persisted encounter undo state gecersiz."
                )
            db.execute(
                "DELETE FROM encounter_undo_history WHERE id = ?",
                (row["id"],),
            )
        return {
            "id": row["id"],
            "command_type": row["command_type"],
            "state": state,
            "created_at": row["created_at"],
        }

    def encounter_undo_count(self, game_id: str) -> int:
        with self.connect() as db:
            return int(
                db.execute(
                    """
                    SELECT COUNT(*) FROM encounter_undo_history
                    WHERE game_id = ?
                    """,
                    (game_id,),
                ).fetchone()[0]
            )

    def campaign(self, campaign_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Kampanya bulunamadı.")
        result = dict(row)
        try:
            settings = json.loads(result.pop("settings_json") or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Campaign settings JSON gecersiz.") from error
        if not isinstance(settings, dict):
            raise RuntimeError("Campaign settings obje olmali.")
        result["settings"] = settings
        return result

    def campaign_for_game(self, game_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                """SELECT campaigns.* FROM campaigns
                JOIN games ON games.campaign_id = campaigns.id
                WHERE games.id = ?""",
                (game_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Kampanya bulunamadı.")
        result = dict(row)
        try:
            settings = json.loads(result.pop("settings_json") or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Campaign settings JSON gecersiz.") from error
        if not isinstance(settings, dict):
            raise RuntimeError("Campaign settings obje olmali.")
        result["settings"] = settings
        return result

    @staticmethod
    def _session_result(row: sqlite3.Row) -> dict:
        result = dict(row)
        try:
            summary = json.loads(result.pop("summary_json") or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Session summary JSON gecersiz.") from error
        result["summary"] = validated_session_summary(summary)
        return result

    def sessions(self, campaign_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM sessions WHERE campaign_id = ? ORDER BY number",
                (campaign_id,),
            ).fetchall()
        return [self._session_result(row) for row in rows]

    def active_session(self, game_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                """SELECT sessions.* FROM sessions
                JOIN games ON games.active_session_id = sessions.id
                WHERE games.id = ?""",
                (game_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Aktif oturum bulunamadı.")
        return self._session_result(row)

    def campaign_members(self, campaign_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT campaign_members.campaign_id,
                    campaign_members.member_id, members.name, members.role,
                    campaign_members.readiness_status,
                    campaign_members.joined_at
                FROM campaign_members
                JOIN members ON members.id = campaign_members.member_id
                WHERE campaign_members.campaign_id = ?
                ORDER BY campaign_members.joined_at""",
                (campaign_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def campaign_lobby(self, auth: AuthContext) -> dict:
        campaign = self.campaign_for_game(auth.game_id)
        session = self.active_session(auth.game_id)
        privileged = auth.role in {"dm", "co_dm"}
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT campaign_members.member_id, members.name, members.role,
                       campaign_members.readiness_status,
                       campaign_members.readiness_version,
                       campaign_members.consent_status,
                       campaign_members.safety_preferences_json,
                       campaign_members.updated_at
                FROM campaign_members
                JOIN members ON members.id = campaign_members.member_id
                WHERE campaign_members.campaign_id = ?
                ORDER BY campaign_members.joined_at
                """,
                (campaign["id"],),
            ).fetchall()
        members = []
        for row in rows:
            member = {
                "member_id": row["member_id"],
                "name": row["name"],
                "role": row["role"],
                "readiness_status": row["readiness_status"],
                "readiness_version": row["readiness_version"],
                "consent_status": row["consent_status"],
                "updated_at": row["updated_at"],
            }
            if privileged or row["member_id"] == auth.member_id:
                try:
                    preferences = json.loads(
                        row["safety_preferences_json"] or "{}"
                    )
                except json.JSONDecodeError as error:
                    raise RuntimeError(
                        "Session Zero safety preferences JSON gecersiz."
                    ) from error
                if not isinstance(preferences, dict):
                    raise RuntimeError(
                        "Session Zero safety preferences obje olmali."
                    )
                member["safety_preferences"] = preferences
            members.append(member)
        return {
            "campaign_id": campaign["id"],
            "settings": campaign["settings"],
            "settings_version": campaign["settings_version"],
            "scheduled_at": session["scheduled_at"],
            "members": members,
        }

    def update_campaign_settings(
        self, game_id: str, expected_version: int, settings: dict
    ) -> dict:
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE campaigns
                SET settings_json = ?, settings_version = settings_version + 1,
                    updated_at = ?
                WHERE id = (SELECT campaign_id FROM games WHERE id = ?)
                  AND settings_version = ?
                """,
                (
                    json.dumps(settings, ensure_ascii=False),
                    timestamp,
                    game_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                actual = db.execute(
                    """
                    SELECT campaigns.settings_version
                    FROM campaigns JOIN games ON games.campaign_id = campaigns.id
                    WHERE games.id = ?
                    """,
                    (game_id,),
                ).fetchone()
                if actual is None:
                    raise KeyError("Campaign bulunamadi.")
                raise ValueError(
                    f"Campaign settings version conflict: expected "
                    f"{expected_version}, actual {actual['settings_version']}."
                )
        return self.campaign_for_game(game_id)

    def update_session_zero_member(
        self,
        game_id: str,
        member_id: str,
        expected_version: int,
        readiness_status: str,
        consent_status: str,
        preferences: dict,
    ) -> dict:
        if readiness_status == "ready" and consent_status != "accepted":
            raise ValueError("Ready olmak icin Session Zero onayi gerekir.")
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE campaign_members
                SET readiness_status = ?, consent_status = ?,
                    safety_preferences_json = ?,
                    readiness_version = readiness_version + 1,
                    updated_at = ?
                WHERE campaign_id = (
                    SELECT campaign_id FROM games WHERE id = ?
                ) AND member_id = ? AND readiness_version = ?
                """,
                (
                    readiness_status,
                    consent_status,
                    json.dumps(preferences, ensure_ascii=False),
                    timestamp,
                    game_id,
                    member_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                actual = db.execute(
                    """
                    SELECT readiness_version FROM campaign_members
                    WHERE campaign_id = (
                        SELECT campaign_id FROM games WHERE id = ?
                    ) AND member_id = ?
                    """,
                    (game_id, member_id),
                ).fetchone()
                if actual is None:
                    raise KeyError("Campaign member bulunamadi.")
                raise ValueError(
                    f"Readiness version conflict: expected {expected_version}, "
                    f"actual {actual['readiness_version']}."
                )
        with self.connect() as db:
            row = db.execute(
                """
                SELECT readiness_status, readiness_version, consent_status,
                       safety_preferences_json, updated_at
                FROM campaign_members
                WHERE campaign_id = (
                    SELECT campaign_id FROM games WHERE id = ?
                ) AND member_id = ?
                """,
                (game_id, member_id),
            ).fetchone()
        return {
            "member_id": member_id,
            "readiness_status": row["readiness_status"],
            "readiness_version": row["readiness_version"],
            "consent_status": row["consent_status"],
            "safety_preferences": json.loads(row["safety_preferences_json"]),
            "updated_at": row["updated_at"],
        }

    def schedule_active_session(
        self, game_id: str, scheduled_at: str | None
    ) -> dict:
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE sessions
                SET scheduled_at = ?, updated_at = ?
                WHERE id = (
                    SELECT active_session_id FROM games WHERE id = ?
                ) AND status = 'preparing'
                """,
                (scheduled_at, now(), game_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "Yalnizca preparing durumundaki aktif session planlanabilir."
                )
        return self.active_session(game_id)

    def create_session(self, game_id: str, title: str | None = None) -> dict:
        with self.transaction():
            with self.connect() as db:
                game = db.execute(
                    """SELECT campaign_id, active_session_id FROM games
                    WHERE id = ?""",
                    (game_id,),
                ).fetchone()
                if game is None:
                    raise KeyError("Oyun bulunamadı.")
                active = db.execute(
                    "SELECT status FROM sessions WHERE id = ?",
                    (game["active_session_id"],),
                ).fetchone()
                if active is not None and active["status"] != "completed":
                    raise ValueError("Yeni oturum için aktif oturum tamamlanmalıdır.")
                number = db.execute(
                    "SELECT COALESCE(MAX(number), 0) + 1 FROM sessions WHERE campaign_id = ?",
                    (game["campaign_id"],),
                ).fetchone()[0]
                session_id, timestamp = uuid4().hex, now()
                session_title = (title or "").strip() or f"Session {number}"
                db.execute(
                    """INSERT INTO sessions
                    (id, campaign_id, number, title, status, scheduled_at,
                     started_at, ended_at, summary_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'preparing', NULL, NULL, NULL, '{}', ?, ?)""",
                    (
                        session_id, game["campaign_id"], number, session_title,
                        timestamp, timestamp,
                    ),
                )
                db.execute(
                    """UPDATE games SET active_session_id = ?, updated_at = ?
                    WHERE id = ?""",
                    (session_id, timestamp, game_id),
                )
            return self.active_session(game_id)

    def set_session_status(self, game_id: str, status: str) -> dict:
        transitions = {
            "preparing": {"live"},
            "live": {"paused", "completed"},
            "paused": {"live", "completed"},
            "completed": set(),
        }
        if status not in transitions:
            raise ValueError("Geçersiz oturum durumu.")
        with self.transaction():
            with self.connect() as db:
                row = db.execute(
                    """SELECT sessions.* FROM sessions
                    JOIN games ON games.active_session_id = sessions.id
                    WHERE games.id = ?""",
                    (game_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("Aktif oturum bulunamadı.")
                if status == row["status"]:
                    raise ValueError("Oturum zaten istenen durumda.")
                if status not in transitions[row["status"]]:
                    raise ValueError(
                        f"Oturum geçişi desteklenmiyor: {row['status']} -> {status}."
                    )
                timestamp = now()
                started_at = (
                    timestamp
                    if status == "live" and row["started_at"] is None
                    else row["started_at"]
                )
                ended_at = timestamp if status == "completed" else row["ended_at"]
                db.execute(
                    """UPDATE sessions SET status = ?, started_at = ?,
                    ended_at = ?, updated_at = ? WHERE id = ?""",
                    (status, started_at, ended_at, timestamp, row["id"]),
                )
            return self.active_session(game_id)

    def session_workspace(self, auth: AuthContext) -> dict:
        session = self.active_session(auth.game_id)
        privileged = auth.role in {"dm", "co_dm"}
        with self.connect() as db:
            note_rows = db.execute(
                """
                SELECT session_notes.*, members.name AS author_name
                FROM session_notes
                JOIN members ON members.id = session_notes.author_id
                WHERE session_notes.session_id = ?
                AND (
                    ? = 1 OR session_notes.visibility = 'party'
                    OR session_notes.visibility = ?
                )
                ORDER BY session_notes.created_at DESC LIMIT 500
                """,
                (
                    session["id"],
                    1 if privileged else 0,
                    f"player:{auth.member_id}",
                ),
            ).fetchall()
            loot_rows = db.execute(
                """
                SELECT session_loot.*, members.name AS claimant_name
                FROM session_loot
                LEFT JOIN members ON members.id = session_loot.claimant_id
                WHERE session_loot.session_id = ?
                ORDER BY session_loot.created_at DESC LIMIT 500
                """,
                (session["id"],),
            ).fetchall()
            quest_rows = db.execute(
                """
                SELECT * FROM session_quests
                WHERE session_id = ? ORDER BY created_at DESC LIMIT 500
                """,
                (session["id"],),
            ).fetchall()
        notes = [
            {
                "id": row["id"],
                "author_id": row["author_id"],
                "author_name": row["author_name"],
                "visibility": row["visibility"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in reversed(note_rows)
        ]
        summary = session["summary"]
        visible_summary = (
            summary
            if privileged or summary.get("published") is True
            else None
        )
        return {
            "session": session,
            "notes": notes,
            "loot": [dict(row) for row in reversed(loot_rows)],
            "quests": [dict(row) for row in reversed(quest_rows)],
            "summary": visible_summary,
        }

    def _mutable_active_session(self, game_id: str) -> dict:
        session = self.active_session(game_id)
        if session["status"] == "completed":
            raise ValueError("Tamamlanan session degistirilemez.")
        return session

    def add_session_note(
        self, auth: AuthContext, content: str, visibility: str
    ) -> dict:
        content = content.strip()
        if not 1 <= len(content) <= 4000:
            raise ValueError("Session note 1..4000 karakter olmali.")
        if visibility not in {"party", "dm_only", "private"}:
            raise ValueError("Session note visibility gecersiz.")
        session = self._mutable_active_session(auth.game_id)
        note_id, timestamp = uuid4().hex, now()
        stored_visibility = (
            f"player:{auth.member_id}"
            if visibility == "private"
            else visibility
        )
        if stored_visibility == "dm_only" and auth.role == "player":
            stored_visibility = f"player:{auth.member_id}"
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO session_notes (
                    id, session_id, author_id, visibility, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    session["id"],
                    auth.member_id,
                    stored_visibility,
                    content,
                    timestamp,
                ),
            )
        return {
            "id": note_id,
            "session_id": session["id"],
            "author_id": auth.member_id,
            "visibility": stored_visibility,
            "content": content,
            "created_at": timestamp,
        }

    def add_session_loot(
        self, game_id: str, actor_id: str, name: str, quantity: int
    ) -> dict:
        session = self._mutable_active_session(game_id)
        loot_id, timestamp = uuid4().hex, now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO session_loot (
                    id, session_id, name, quantity, status, claimant_id,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'available', NULL, ?, ?, ?)
                """,
                (
                    loot_id, session["id"], name.strip(), quantity,
                    actor_id, timestamp, timestamp,
                ),
            )
        return {
            "id": loot_id, "name": name.strip(), "quantity": quantity,
            "status": "available", "claimant_id": None,
        }

    def claim_session_loot(
        self, game_id: str, member_id: str, loot_id: str
    ) -> dict:
        session = self._mutable_active_session(game_id)
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE session_loot
                SET status = 'claimed', claimant_id = ?, updated_at = ?
                WHERE id = ? AND session_id = ? AND status = 'available'
                """,
                (member_id, now(), loot_id, session["id"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Loot bulunamadi veya daha once claim edildi.")
            row = db.execute(
                "SELECT * FROM session_loot WHERE id = ?", (loot_id,)
            ).fetchone()
        return dict(row)

    def add_session_quest(
        self, game_id: str, actor_id: str, title: str, description: str
    ) -> dict:
        session = self._mutable_active_session(game_id)
        quest_id, timestamp = uuid4().hex, now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO session_quests (
                    id, session_id, title, description, status,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    quest_id, session["id"], title.strip(), description,
                    actor_id, timestamp, timestamp,
                ),
            )
        return {
            "id": quest_id, "title": title.strip(),
            "description": description, "status": "active",
        }

    def set_session_quest_status(
        self, game_id: str, quest_id: str, status: str
    ) -> dict:
        session = self._mutable_active_session(game_id)
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE session_quests SET status = ?, updated_at = ?
                WHERE id = ? AND session_id = ?
                """,
                (status, now(), quest_id, session["id"]),
            )
            if cursor.rowcount != 1:
                raise KeyError("Quest bulunamadi.")
            row = db.execute(
                "SELECT * FROM session_quests WHERE id = ?", (quest_id,)
            ).fetchone()
        return dict(row)

    def update_session_summary(self, game_id: str, summary: dict) -> dict:
        summary = validated_session_summary(summary)
        session = self.active_session(game_id)
        with self.connect() as db:
            db.execute(
                """
                UPDATE sessions SET summary_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(summary, ensure_ascii=False),
                    now(),
                    session["id"],
                ),
            )
        return self.active_session(game_id)

    def assign_co_dm(self, game_id: str, member_id: str | None) -> None:
        with self.connect() as db:
            db.execute("UPDATE members SET role = 'player' WHERE game_id = ? AND role = 'co_dm'", (game_id,))
            if member_id:
                row = db.execute("SELECT role FROM members WHERE game_id = ? AND id = ?", (game_id, member_id)).fetchone()
                if row is None or row["role"] == "dm":
                    raise KeyError("Co-DM adayı bulunamadı.")
                db.execute("UPDATE members SET role = 'co_dm' WHERE game_id = ? AND id = ?", (game_id, member_id))

    def set_fallback_mode(self, game_id: str, mode: str) -> None:
        if mode not in {"assisted", "vote_ai"}:
            raise ValueError("Fallback modu assisted veya vote_ai olmalıdır.")
        with self.connect() as db:
            db.execute("UPDATE games SET fallback_dm_mode = ? WHERE id = ?", (mode, game_id))

    def set_handover(self, game_id: str, handover: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE games SET handover_json = ?, updated_at = ? WHERE id = ?", (json.dumps(handover), now(), game_id))

    def cancel_handover(self, game_id: str) -> None:
        self.set_handover(game_id, {})

    def recoverable_grace_handovers(self) -> list[tuple[str, str, float]]:
        """Return persisted grace deadlines for shared-runtime recovery."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, handover_json FROM games"
            ).fetchall()
        result: list[tuple[str, str, float]] = []
        for row in rows:
            try:
                handover = json.loads(row["handover_json"] or "{}")
                deadline = datetime.fromisoformat(
                    handover.get("deadline", "")
                )
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                continue
            offline_dm_id = handover.get("offline_dm_id")
            if (
                handover.get("status") == "grace"
                and isinstance(offline_dm_id, str)
                and deadline.tzinfo is not None
            ):
                result.append(
                    (row["id"], offline_dm_id, deadline.timestamp())
                )
        return result

    def activate_dm(self, game_id: str, member_id: str, mode: str = "human") -> None:
        member = self.member(game_id, member_id)
        if member["role"] not in {"dm", "co_dm"}:
            raise ValueError("Yalnızca DM veya co-DM kontrolü devralabilir.")
        with self.connect() as db:
            db.execute("UPDATE games SET active_dm_id = ?, dm_mode = ?, handover_json = '{}', updated_at = ? WHERE id = ?", (member_id, mode, now(), game_id))

    def set_dm_mode(self, game_id: str, mode: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE games SET dm_mode = ?, updated_at = ? WHERE id = ?", (mode, now(), game_id))

    def dice_preferences(self, auth: AuthContext) -> dict:
        with self.connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO member_dice_preferences (
                    member_id, game_id, theme, sound_enabled, updated_at
                ) VALUES (?, ?, 'crimson', 1, ?)
                """,
                (auth.member_id, auth.game_id, now()),
            )
            row = db.execute(
                """
                SELECT theme, sound_enabled, updated_at
                FROM member_dice_preferences
                WHERE member_id = ? AND game_id = ?
                """,
                (auth.member_id, auth.game_id),
            ).fetchone()
        if row is None:
            raise ValueError("Zar tercihleri bulunamadi.")
        return {
            "theme": row["theme"],
            "sound_enabled": bool(row["sound_enabled"]),
            "updated_at": row["updated_at"],
        }

    def update_dice_preferences(
        self, auth: AuthContext, theme: str, sound_enabled: bool
    ) -> dict:
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO member_dice_preferences (
                    member_id, game_id, theme, sound_enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(member_id) DO UPDATE SET
                    game_id = excluded.game_id,
                    theme = excluded.theme,
                    sound_enabled = excluded.sound_enabled,
                    updated_at = excluded.updated_at
                WHERE member_dice_preferences.game_id = excluded.game_id
                """,
                (
                    auth.member_id,
                    auth.game_id,
                    theme,
                    int(sound_enabled),
                    timestamp,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError("Zar tercihleri uye kapsami disinda.")
        return {
            "theme": theme,
            "sound_enabled": sound_enabled,
            "updated_at": timestamp,
        }

    @staticmethod
    def _map_asset_result(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "original_name": row["original_name"],
            "content_type": row["content_type"],
            "byte_size": row["byte_size"],
            "width": row["width"],
            "height": row["height"],
            "created_at": row["created_at"],
            "url": f"/api/maps/assets/{row['id']}/content",
        }

    def create_map_asset(
        self,
        auth: AuthContext,
        original_name: str,
        storage_key: str,
        metadata: dict,
    ) -> dict:
        campaign_id = self.game(auth.game_id)["campaign_id"]
        timestamp = now()
        asset_id = uuid4().hex
        safe_name = original_name.strip()[:160] or f"map.{metadata['extension']}"
        with self.connect() as db:
            total = int(
                db.execute(
                    """
                    SELECT COALESCE(SUM(byte_size), 0)
                    FROM map_assets WHERE campaign_id = ?
                    """,
                    (campaign_id,),
                ).fetchone()[0]
            )
            if total + int(metadata["byte_size"]) > 100 * 1024 * 1024:
                raise ValueError("Kampanya harita depolama limiti asildi.")
            db.execute(
                """
                INSERT INTO map_assets (
                    id, campaign_id, uploader_id, original_name, storage_key,
                    sha256, content_type, byte_size, width, height, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    campaign_id,
                    auth.member_id,
                    safe_name,
                    storage_key,
                    metadata["sha256"],
                    metadata["content_type"],
                    metadata["byte_size"],
                    metadata["width"],
                    metadata["height"],
                    timestamp,
                ),
            )
            row = db.execute(
                "SELECT * FROM map_assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return self._map_asset_result(row)

    def map_assets(self, auth: AuthContext) -> list[dict]:
        campaign_id = self.game(auth.game_id)["campaign_id"]
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM map_assets
                WHERE campaign_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 100
                """,
                (campaign_id,),
            ).fetchall()
        return [self._map_asset_result(row) for row in rows]

    def map_asset_content(self, auth: AuthContext, asset_id: str) -> dict:
        campaign_id = self.game(auth.game_id)["campaign_id"]
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM map_assets
                WHERE id = ? AND campaign_id = ?
                """,
                (asset_id, campaign_id),
            ).fetchone()
            if row is None:
                raise KeyError("Harita asset bulunamadi.")
            if auth.role == "player":
                visible = db.execute(
                    """
                    SELECT map_fog_state.enabled AS fog_enabled,
                           map_fog_state.revision AS fog_revision,
                           map_scenes.grid_size_px,
                           map_scenes.revision AS scene_revision
                    FROM map_scenes
                    JOIN map_fog_state
                      ON map_fog_state.game_id = map_scenes.game_id
                    WHERE map_scenes.game_id = ? AND map_scenes.campaign_id = ?
                      AND map_scenes.asset_id = ? AND map_scenes.published = 1
                    """,
                    (auth.game_id, campaign_id, asset_id),
                ).fetchone()
                if visible is None:
                    raise KeyError("Harita asset bulunamadi.")
        return {
            **self._map_asset_result(row),
            "storage_key": row["storage_key"],
            "sha256": row["sha256"],
            "fog": (
                {
                    "enabled": bool(visible["fog_enabled"]),
                    "revision": int(visible["fog_revision"]),
                    "grid_size_px": int(visible["grid_size_px"]),
                    "scene_revision": int(visible["scene_revision"]),
                }
                if auth.role == "player"
                else None
            ),
        }

    def map_scene(
        self, auth: AuthContext, game: dict | None = None
    ) -> dict:
        with self.read_transaction():
            return self._map_scene(auth, game)

    def _map_scene(
        self, auth: AuthContext, game: dict | None = None
    ) -> dict:
        game = game or self.game(auth.game_id)
        with self.connect() as db:
            row = db.execute(
                """
                SELECT map_scenes.*, map_assets.original_name,
                       map_assets.width AS asset_width,
                       map_assets.height AS asset_height,
                       map_fog_state.enabled AS fog_enabled,
                       map_fog_state.revision AS fog_revision,
                       map_fog_state.updated_at AS fog_updated_at
                FROM map_scenes
                LEFT JOIN map_assets ON map_assets.id = map_scenes.asset_id
                JOIN map_fog_state
                  ON map_fog_state.game_id = map_scenes.game_id
                WHERE map_scenes.game_id = ?
                """,
                (auth.game_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Map scene kaydi bulunamadi.")
        asset_id = row["asset_id"]
        published = bool(row["published"])
        if auth.role == "player" and not published:
            return {
                "name": "Battle Map",
                "asset_id": None,
                "asset": None,
                "grid_type": "none",
                "grid_size_px": 70,
                "distance_per_cell": 5,
                "distance_unit": "ft",
                "viewport": {"x": 0, "y": 0, "zoom": 1},
                "published": False,
                "tokens": [],
                "fog": {
                    "enabled": False,
                    "revision": 0,
                    "mask_url": None,
                    "revealed_cells": None,
                    "updated_at": row["updated_at"],
                },
                "signals": [],
                "revision": row["revision"],
                "updated_at": row["updated_at"],
            }
        fog_cells = None
        if auth.role in {"dm", "co_dm"}:
            with self.connect() as db:
                fog_cells = [
                    [cell["cell_x"], cell["cell_y"]]
                    for cell in db.execute(
                        """
                        SELECT cell_x, cell_y FROM map_fog_cells
                        WHERE game_id = ? ORDER BY cell_y, cell_x
                        """,
                        (auth.game_id,),
                    ).fetchall()
                ]
        result = {
            "name": row["name"],
            "asset_id": asset_id,
            "asset": (
                {
                    "id": asset_id,
                    "original_name": row["original_name"],
                    "width": row["asset_width"],
                    "height": row["asset_height"],
                    "url": (
                        f"/api/maps/assets/{asset_id}/content"
                        + (
                            f"?fog={row['fog_revision']}"
                            f"&scene={row['revision']}"
                            if (
                                auth.role == "player"
                                and bool(row["fog_enabled"])
                            )
                            else ""
                        )
                    ),
                }
                if asset_id is not None
                else None
            ),
            "grid_type": row["grid_type"],
            "grid_size_px": row["grid_size_px"],
            "distance_per_cell": row["distance_per_cell"],
            "distance_unit": row["distance_unit"],
            "viewport": {
                "x": row["viewport_x"],
                "y": row["viewport_y"],
                "zoom": row["viewport_zoom"],
            },
            "published": published,
            "tokens": self.map_tokens(
                auth,
                game["state"],
                published,
                active_dm_id=game["active_dm_id"],
            ),
            "fog": {
                "enabled": bool(row["fog_enabled"]),
                "revision": row["fog_revision"],
                "mask_url": (
                    f"/api/maps/fog-mask?fog={row['fog_revision']}"
                    f"&scene={row['revision']}"
                    if bool(row["fog_enabled"]) and asset_id is not None
                    else None
                ),
                "revealed_cells": fog_cells,
                "updated_at": row["fog_updated_at"],
            },
            "signals": self.map_transients(
                auth, published, asset_id is not None
            ),
            "revision": row["revision"],
            "updated_at": row["updated_at"],
        }
        return result

    def map_transients(
        self, auth: AuthContext, scene_published: bool, has_asset: bool
    ) -> list[dict]:
        if not has_asset or (auth.role == "player" and not scene_published):
            return []
        timestamp = now()
        with self.connect() as db:
            revealed_cells: set[tuple[int, int]] | None = None
            grid_size = 1
            if auth.role == "player":
                fog = db.execute(
                    """
                    SELECT map_fog_state.enabled, map_scenes.grid_size_px
                    FROM map_fog_state
                    JOIN map_scenes
                      ON map_scenes.game_id = map_fog_state.game_id
                    WHERE map_fog_state.game_id = ?
                    """,
                    (auth.game_id,),
                ).fetchone()
                if fog is None:
                    raise RuntimeError("Map fog kaydi bulunamadi.")
                if bool(fog["enabled"]):
                    grid_size = int(fog["grid_size_px"])
                    revealed_cells = {
                        (cell["cell_x"], cell["cell_y"])
                        for cell in db.execute(
                            """
                            SELECT cell_x, cell_y FROM map_fog_cells
                            WHERE game_id = ?
                            """,
                            (auth.game_id,),
                        ).fetchall()
                    }
            rows = db.execute(
                """
                SELECT map_transients.*, members.name AS actor_name
                FROM map_transients
                JOIN members ON members.id = map_transients.actor_id
                WHERE map_transients.game_id = ?
                  AND map_transients.expires_at > ?
                ORDER BY map_transients.created_at, map_transients.id
                LIMIT 100
                """,
                (auth.game_id, timestamp),
            ).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    "Persisted map transient payload gecersiz."
                ) from error
            if not isinstance(payload, dict):
                raise RuntimeError("Persisted map transient payload gecersiz.")
            payload, points = self._validated_transient_payload(
                row["kind"], payload
            )
            if revealed_cells is not None and not self._transient_revealed(
                points, grid_size, revealed_cells
            ):
                continue
            result.append({
                "id": row["id"],
                "kind": row["kind"],
                "actor_id": row["actor_id"],
                "actor_name": row["actor_name"],
                "payload": payload,
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
            })
        return result

    @staticmethod
    def _validated_transient_payload(
        kind: str,
        payload: dict,
    ) -> tuple[dict, list[list[int | float]]]:
        if kind == "ping":
            if set(payload) != {"x", "y"}:
                raise RuntimeError("Persisted map transient payload gecersiz.")
            points = [[payload["x"], payload["y"]]]
        elif kind == "draw":
            if set(payload) != {"points"}:
                raise RuntimeError("Persisted map transient payload gecersiz.")
            points = payload["points"]
        else:
            raise RuntimeError("Persisted map transient kind gecersiz.")
        if (
            not isinstance(points, list)
            or not (1 if kind == "ping" else 2)
            <= len(points)
            <= (1 if kind == "ping" else 64)
            or any(
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not 0 <= float(value) <= 100_000
                    for value in point
                )
                for point in points
            )
        ):
            raise RuntimeError("Persisted map transient payload gecersiz.")
        clean_points = [[point[0], point[1]] for point in points]
        clean_payload = (
            {"x": clean_points[0][0], "y": clean_points[0][1]}
            if kind == "ping"
            else {"points": clean_points}
        )
        return clean_payload, clean_points

    @staticmethod
    def _transient_revealed(
        points: list[list[int | float]],
        grid_size: int,
        revealed_cells: set[tuple[int, int]],
    ) -> bool:
        def revealed(x: float, y: float) -> bool:
            return (
                int(float(x) // grid_size),
                int(float(y) // grid_size),
            ) in revealed_cells

        previous = points[0]
        if not revealed(previous[0], previous[1]):
            return False
        for point in points[1:]:
            min_x = int(min(float(previous[0]), float(point[0])) // grid_size)
            max_x = int(max(float(previous[0]), float(point[0])) // grid_size)
            min_y = int(min(float(previous[1]), float(point[1])) // grid_size)
            max_y = int(max(float(previous[1]), float(point[1])) // grid_size)
            if any(
                (cell_x, cell_y) not in revealed_cells
                for cell_x in range(min_x, max_x + 1)
                for cell_y in range(min_y, max_y + 1)
            ):
                return False
            previous = point
        return True

    def create_map_transient(
        self, auth: AuthContext, kind: str, payload: dict
    ) -> dict:
        with self.connect() as db:
            scene = db.execute(
                """
                SELECT map_scenes.published, map_assets.width, map_assets.height
                FROM map_scenes
                JOIN map_assets ON map_assets.id = map_scenes.asset_id
                  AND map_assets.campaign_id = map_scenes.campaign_id
                WHERE map_scenes.game_id = ?
                """,
                (auth.game_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("Map sinyali icin scene asset gerekli.")
            if auth.role == "player" and not bool(scene["published"]):
                raise PermissionError("Yayinlanmamis haritada sinyal gonderilemez.")
            points = (
                [[payload["x"], payload["y"]]]
                if kind == "ping"
                else payload["points"]
            )
            if any(
                not 0 <= float(point[0]) <= float(scene["width"])
                or not 0 <= float(point[1]) <= float(scene["height"])
                for point in points
            ):
                raise ValueError("Map sinyali harita disinda.")
            transient_id = uuid4().hex
            created_at = datetime.now(UTC)
            expires_at = created_at + timedelta(
                seconds=6 if kind == "ping" else 30
            )
            db.execute(
                """
                DELETE FROM map_transients
                WHERE game_id = ? AND expires_at <= ?
                """,
                (auth.game_id, created_at.isoformat()),
            )
            count = int(
                db.execute(
                    """
                    SELECT COUNT(*) FROM map_transients
                    WHERE game_id = ?
                    """,
                    (auth.game_id,),
                ).fetchone()[0]
            )
            if count >= 100:
                raise ValueError("Aktif map sinyali limiti asildi.")
            db.execute(
                """
                INSERT INTO map_transients (
                    id, game_id, actor_id, kind, payload_json,
                    expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transient_id,
                    auth.game_id,
                    auth.member_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    expires_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
        return {
            "id": transient_id,
            "kind": kind,
            "actor_id": auth.member_id,
            "payload": payload,
            "expires_at": expires_at.isoformat(),
            "created_at": created_at.isoformat(),
        }

    def set_map_fog(
        self, auth: AuthContext, expected_revision: int, enabled: bool
    ) -> dict:
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE map_fog_state
                SET enabled = ?, revision = revision + 1, updated_at = ?
                WHERE game_id = ? AND revision = ?
                """,
                (int(enabled), now(), auth.game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                current = db.execute(
                    "SELECT revision FROM map_fog_state WHERE game_id = ?",
                    (auth.game_id,),
                ).fetchone()
                raise MapFogConflict(
                    expected_revision,
                    int(current["revision"]) if current else 0,
                )
        return self.map_scene(auth)

    def paint_map_fog(
        self,
        auth: AuthContext,
        expected_revision: int,
        mode: str,
        cells: list[list[int]],
    ) -> dict:
        game = self.game(auth.game_id)
        with self.connect() as db:
            scene = db.execute(
                """
                SELECT map_scenes.grid_size_px,
                       map_assets.width, map_assets.height
                FROM map_scenes
                JOIN map_assets ON map_assets.id = map_scenes.asset_id
                  AND map_assets.campaign_id = map_scenes.campaign_id
                WHERE map_scenes.game_id = ?
                """,
                (auth.game_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("Fog boyamak icin map asset gerekli.")
            grid_size = int(scene["grid_size_px"])
            columns = (int(scene["width"]) + grid_size - 1) // grid_size
            rows = (int(scene["height"]) + grid_size - 1) // grid_size
            if any(
                not 0 <= x < columns or not 0 <= y < rows
                for x, y in cells
            ):
                raise ValueError("Fog cell harita disinda.")
            current = db.execute(
                """
                SELECT revision FROM map_fog_state
                WHERE game_id = ?
                """,
                (auth.game_id,),
            ).fetchone()
            actual = int(current["revision"]) if current else 0
            if actual != expected_revision:
                raise MapFogConflict(expected_revision, actual)
            timestamp = now()
            if mode == "reveal":
                db.executemany(
                    """
                    INSERT INTO map_fog_cells (
                        game_id, cell_x, cell_y, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(game_id, cell_x, cell_y)
                    DO UPDATE SET updated_at = excluded.updated_at
                    """,
                    [
                        (auth.game_id, x, y, timestamp)
                        for x, y in cells
                    ],
                )
            else:
                db.executemany(
                    """
                    DELETE FROM map_fog_cells
                    WHERE game_id = ? AND cell_x = ? AND cell_y = ?
                    """,
                    [(auth.game_id, x, y) for x, y in cells],
                )
            cursor = db.execute(
                """
                UPDATE map_fog_state
                SET revision = revision + 1, updated_at = ?
                WHERE game_id = ? AND revision = ?
                """,
                (timestamp, auth.game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                latest = db.execute(
                    "SELECT revision FROM map_fog_state WHERE game_id = ?",
                    (auth.game_id,),
                ).fetchone()
                raise MapFogConflict(
                    expected_revision,
                    int(latest["revision"]) if latest else 0,
                )
        return self.map_scene(auth, game)

    def map_fog_mask(self, auth: AuthContext) -> dict:
        with self.read_transaction():
            return self._map_fog_mask(auth)

    def _map_fog_mask(self, auth: AuthContext) -> dict:
        game = self.game(auth.game_id)
        with self.connect() as db:
            row = db.execute(
                """
                SELECT map_fog_state.enabled, map_fog_state.revision,
                       map_scenes.published, map_scenes.grid_size_px,
                       map_scenes.revision AS scene_revision,
                       map_assets.width, map_assets.height,
                       map_assets.sha256 AS asset_sha256
                FROM map_fog_state
                JOIN map_scenes ON map_scenes.game_id = map_fog_state.game_id
                JOIN map_assets ON map_assets.id = map_scenes.asset_id
                  AND map_assets.campaign_id = map_scenes.campaign_id
                WHERE map_fog_state.game_id = ?
                """,
                (auth.game_id,),
            ).fetchone()
            if (
                row is None
                or not bool(row["enabled"])
                or (auth.role == "player" and not bool(row["published"]))
            ):
                raise KeyError("Fog mask bulunamadi.")
            cells = {
                (cell["cell_x"], cell["cell_y"])
                for cell in db.execute(
                    """
                    SELECT cell_x, cell_y FROM map_fog_cells
                    WHERE game_id = ?
                    """,
                    (auth.game_id,),
                ).fetchall()
            }
        grid_size = int(row["grid_size_px"])
        return {
            "columns": (int(row["width"]) + grid_size - 1) // grid_size,
            "rows": (int(row["height"]) + grid_size - 1) // grid_size,
            "revealed_cells": cells,
            "revision": int(row["revision"]),
            "scene_revision": int(row["scene_revision"]),
            "grid_size_px": grid_size,
            "asset_sha256": row["asset_sha256"],
            "game_revision": int(game["state_revision"]),
        }

    def update_map_scene(
        self, auth: AuthContext, expected_revision: int, payload: dict
    ) -> dict:
        game = self.game(auth.game_id)
        campaign_id = game["campaign_id"]
        asset_id = payload.get("asset_id")
        with self.connect() as db:
            if asset_id is not None:
                exists = db.execute(
                    """
                    SELECT 1 FROM map_assets
                    WHERE id = ? AND campaign_id = ?
                    """,
                    (asset_id, campaign_id),
                ).fetchone()
                if exists is None:
                    raise ValueError("Harita asset kampanyada bulunamadi.")
            cursor = db.execute(
                """
                UPDATE map_scenes SET
                    asset_id = ?, name = ?, grid_type = ?, grid_size_px = ?,
                    distance_per_cell = ?, distance_unit = ?,
                    viewport_x = ?, viewport_y = ?, viewport_zoom = ?,
                    published = ?, revision = revision + 1, updated_at = ?
                WHERE game_id = ? AND campaign_id = ? AND revision = ?
                """,
                (
                    asset_id,
                    payload["name"],
                    payload["grid_type"],
                    payload["grid_size_px"],
                    payload["distance_per_cell"],
                    payload["distance_unit"],
                    payload["viewport"]["x"],
                    payload["viewport"]["y"],
                    payload["viewport"]["zoom"],
                    int(payload["published"]),
                    now(),
                    auth.game_id,
                    campaign_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                current = db.execute(
                    "SELECT revision FROM map_scenes WHERE game_id = ?",
                    (auth.game_id,),
                ).fetchone()
                actual = int(current["revision"]) if current else 0
                raise MapSceneConflict(expected_revision, actual)
        return self.map_scene(auth)

    def sync_map_tokens(self, auth: AuthContext, state: dict) -> list[dict]:
        if state.get("encounter_status") not in {"active", "paused"}:
            raise ValueError("Map tokenlari icin canli encounter gerekli.")
        combatants = state.get("combatants", [])
        if not combatants:
            raise ValueError("Map tokenlari icin combatant gerekli.")
        game = self.game(auth.game_id)
        campaign_id = game["campaign_id"]
        with self.connect() as db:
            scene = db.execute(
                """
                SELECT map_scenes.*, map_assets.width AS asset_width,
                       map_assets.height AS asset_height
                FROM map_scenes
                LEFT JOIN map_assets
                  ON map_assets.id = map_scenes.asset_id
                 AND map_assets.campaign_id = map_scenes.campaign_id
                WHERE map_scenes.game_id = ?
                """,
                (auth.game_id,),
            ).fetchone()
            if scene is None or scene["asset_id"] is None:
                raise ValueError("Token yerlestirmek icin map scene asset gerekli.")
            owner_rows = db.execute(
                """
                SELECT id, character_id FROM members
                WHERE game_id = ? AND character_id IS NOT NULL
                """,
                (auth.game_id,),
            ).fetchall()
            owners = {row["character_id"]: row["id"] for row in owner_rows}
            combatant_ids = [str(item["id"]) for item in combatants]
            placeholders = ",".join("?" for _ in combatant_ids)
            db.execute(
                f"""
                DELETE FROM map_tokens
                WHERE game_id = ? AND combatant_id NOT IN ({placeholders})
                """,
                (auth.game_id, *combatant_ids),
            )
            grid_size = int(scene["grid_size_px"])
            token_size = max(24, min(160, int(grid_size * 0.8)))
            width = int(scene["asset_width"])
            height = int(scene["asset_height"])
            half_size = token_size / 2
            minimum_x = min(half_size, width / 2)
            maximum_x = max(minimum_x, width - half_size)
            minimum_y = min(half_size, height / 2)
            maximum_y = max(minimum_y, height - half_size)
            columns = max(1, width // max(grid_size, 1))
            timestamp = now()
            for index, combatant in enumerate(combatants):
                column, row_index = index % columns, index // columns
                x = min(
                    maximum_x,
                    max(minimum_x, grid_size / 2 + column * grid_size),
                )
                y = min(
                    maximum_y,
                    max(minimum_y, grid_size / 2 + row_index * grid_size),
                )
                owner_member_id = owners.get(str(combatant["id"]))
                db.execute(
                    """
                    INSERT INTO map_tokens (
                        id, game_id, campaign_id, combatant_id,
                        owner_member_id, x, y, size_px, revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(game_id, combatant_id) DO UPDATE SET
                        owner_member_id = excluded.owner_member_id,
                        x = MIN(?, MAX(?, map_tokens.x)),
                        y = MIN(?, MAX(?, map_tokens.y)),
                        size_px = excluded.size_px,
                        revision = map_tokens.revision + 1,
                        updated_at = excluded.updated_at
                    WHERE map_tokens.owner_member_id IS NOT excluded.owner_member_id
                       OR map_tokens.x < ? OR map_tokens.x > ?
                       OR map_tokens.y < ? OR map_tokens.y > ?
                       OR map_tokens.size_px != excluded.size_px
                    """,
                    (
                        uuid4().hex,
                        auth.game_id,
                        campaign_id,
                        str(combatant["id"]),
                        owner_member_id,
                        x,
                        y,
                        token_size,
                        timestamp,
                        timestamp,
                        maximum_x,
                        minimum_x,
                        maximum_y,
                        minimum_y,
                        minimum_x,
                        maximum_x,
                        minimum_y,
                        maximum_y,
                    ),
                )
        return self.map_tokens(auth, state, True)

    def map_tokens(
        self,
        auth: AuthContext,
        state: dict,
        scene_published: bool,
        active_dm_id: str | None = None,
    ) -> list[dict]:
        if state.get("encounter_status") not in {"active", "paused"}:
            return []
        if auth.role == "player" and not scene_published:
            return []
        combatants = {
            str(item["id"]): item for item in state.get("combatants", [])
        }
        if active_dm_id is None:
            active_dm_id = self.game(auth.game_id)["active_dm_id"]
        is_active_dm = (
            auth.role in {"dm", "co_dm"}
            and active_dm_id == auth.member_id
        )
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM map_tokens
                WHERE game_id = ?
                ORDER BY created_at, id
                """,
                (auth.game_id,),
            ).fetchall()
        result = []
        for row in rows:
            combatant = combatants.get(row["combatant_id"])
            if combatant is None:
                continue
            hidden = bool(combatant.get("hidden", False))
            if auth.role == "player" and hidden:
                continue
            privileged = auth.role in {"dm", "co_dm"}
            item = {
                "id": row["id"],
                "combatant_id": row["combatant_id"],
                "owner_member_id": (
                    row["owner_member_id"]
                    if privileged or row["owner_member_id"] == auth.member_id
                    else None
                ),
                "name": combatant.get("name", "Token"),
                "kind": combatant.get("kind", "npc"),
                "initiative": int(combatant.get("initiative", 0)),
                "x": row["x"],
                "y": row["y"],
                "size_px": row["size_px"],
                "revision": row["revision"],
                "can_move": (
                    is_active_dm
                    or (
                        auth.role == "player"
                        and row["owner_member_id"] == auth.member_id
                    )
                ),
            }
            if privileged or combatant.get("kind") != "monster":
                if isinstance(combatant.get("hp"), int):
                    item["hp"] = combatant["hp"]
                if isinstance(combatant.get("max_hp"), int):
                    item["max_hp"] = combatant["max_hp"]
            result.append(item)
        return result

    def move_map_token(
        self,
        auth: AuthContext,
        token_id: str,
        expected_revision: int,
        x: float,
        y: float,
    ) -> dict:
        game = self.game(auth.game_id)
        state = game["state"]
        if state.get("encounter_status") not in {"active", "paused"}:
            raise ValueError("Token yalniz canli encounter sirasinda tasinabilir.")
        with self.connect() as db:
            row = db.execute(
                """
                SELECT map_tokens.*, map_scenes.published,
                       map_assets.width AS asset_width,
                       map_assets.height AS asset_height
                FROM map_tokens
                JOIN map_scenes ON map_scenes.game_id = map_tokens.game_id
                JOIN map_assets ON map_assets.id = map_scenes.asset_id
                  AND map_assets.campaign_id = map_scenes.campaign_id
                WHERE map_tokens.id = ? AND map_tokens.game_id = ?
                """,
                (token_id, auth.game_id),
            ).fetchone()
            if row is None:
                raise KeyError("Map token bulunamadi.")
            combatant = next(
                (
                    item for item in state.get("combatants", [])
                    if item.get("id") == row["combatant_id"]
                ),
                None,
            )
            if combatant is None:
                raise KeyError("Token combatant bulunamadi.")
            if auth.role == "player" and (
                not bool(row["published"])
                or row["owner_member_id"] != auth.member_id
                or combatant.get("hidden", False)
            ):
                raise PermissionError("Bu token'i tasima yetkiniz yok.")
            half_size = float(row["size_px"]) / 2
            minimum_x = min(half_size, float(row["asset_width"]) / 2)
            maximum_x = max(
                minimum_x, float(row["asset_width"]) - half_size
            )
            minimum_y = min(half_size, float(row["asset_height"]) / 2)
            maximum_y = max(
                minimum_y, float(row["asset_height"]) - half_size
            )
            if not minimum_x <= x <= maximum_x or not minimum_y <= y <= maximum_y:
                raise ValueError("Token koordinati harita disinda.")
            cursor = db.execute(
                """
                UPDATE map_tokens
                SET x = ?, y = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND game_id = ? AND revision = ?
                """,
                (x, y, now(), token_id, auth.game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                current = db.execute(
                    "SELECT revision FROM map_tokens WHERE id = ? AND game_id = ?",
                    (token_id, auth.game_id),
                ).fetchone()
                actual = int(current["revision"]) if current else 0
                raise MapTokenConflict(expected_revision, actual)
        refreshed = self.map_tokens(auth, state, bool(row["published"]))
        return next(item for item in refreshed if item["id"] == token_id)

    def remove_map_token(
        self, auth: AuthContext, token_id: str, expected_revision: int
    ) -> None:
        with self.connect() as db:
            cursor = db.execute(
                """
                DELETE FROM map_tokens
                WHERE id = ? AND game_id = ? AND revision = ?
                """,
                (token_id, auth.game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                current = db.execute(
                    """
                    SELECT revision FROM map_tokens
                    WHERE id = ? AND game_id = ?
                    """,
                    (token_id, auth.game_id),
                ).fetchone()
                if current is None:
                    raise KeyError("Map token bulunamadi.")
                raise MapTokenConflict(
                    expected_revision, int(current["revision"])
                )

    def add_event(self, game_id: str, event_type: str, actor_id: str, visibility: str, payload: dict) -> dict:
        timestamp = now()
        intent = payload.get("intent") if isinstance(payload, dict) else None
        if (
            isinstance(payload, dict)
            and "intent" in payload
            and not isinstance(intent, dict)
        ):
            raise ValueError("Typed event intent obje olmali.")
        if event_type == "typed_roll_resolved" and not isinstance(intent, dict):
            raise ValueError("Typed roll event intent metadata gerektirir.")
        if intent is not None and (
            not isinstance(intent, dict)
            or not isinstance(intent.get("intent_id"), str)
            or not 16 <= len(intent["intent_id"]) <= 64
            or not isinstance(intent.get("schema_version"), int)
            or intent.get("schema_version") != 1
            or isinstance(intent.get("schema_version"), bool)
        ):
            raise ValueError("Typed event metadata gecersiz.")
        typed_intent_id = (
            intent.get("intent_id") if isinstance(intent, dict) else None
        )
        intent_schema_version = (
            intent.get("schema_version") if isinstance(intent, dict) else None
        )
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO events (
                    game_id, type, actor_id, visibility, payload_json, created_at,
                    typed_intent_id, intent_schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game_id,
                    event_type,
                    actor_id,
                    visibility,
                    json.dumps(payload, ensure_ascii=False),
                    timestamp,
                    typed_intent_id,
                    intent_schema_version,
                ),
            )
        return {"id": cursor.lastrowid, "game_id": game_id, "type": event_type, "actor_id": actor_id, "visibility": visibility, "payload": payload, "created_at": timestamp}

    @staticmethod
    def can_view(visibility: str, auth: AuthContext) -> bool:
        return visibility in {"public", "party"} or auth.role in {"dm", "co_dm"} or visibility == f"player:{auth.member_id}"

    def events(self, auth: AuthContext, after: int = 0, limit: int = 200) -> list[dict]:
        limit = min(500, max(1, int(limit)))
        privileged = 1 if auth.role in {"dm", "co_dm"} else 0
        with self.connect() as db:
            rows = db.execute(
                """SELECT * FROM (
                    SELECT * FROM events
                    WHERE game_id = ? AND id > ?
                    AND (? = 1 OR visibility IN ('public', 'party') OR visibility = ?)
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id""",
                (auth.game_id, after, privileged, f"player:{auth.member_id}", limit),
            ).fetchall()
        return [{"id": row["id"], "type": row["type"], "actor_id": row["actor_id"], "visibility": row["visibility"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]} for row in rows if self.can_view(row["visibility"], auth)]

    @staticmethod
    def _event_result(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "game_id": row["game_id"], "type": row["type"],
            "actor_id": row["actor_id"], "visibility": row["visibility"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    def event_cursor(self, game_id: str) -> int:
        with self.connect() as db:
            return int(
                db.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM events WHERE game_id = ?",
                    (game_id,),
                ).fetchone()[0]
            )

    def event_page(
        self, auth: AuthContext, after: int = 0, limit: int = 100
    ) -> dict:
        after = max(0, int(after))
        limit = min(500, max(1, int(limit)))
        privileged = 1 if auth.role in {"dm", "co_dm"} else 0
        with self.connect() as db:
            rows = db.execute(
                """SELECT * FROM events
                WHERE game_id = ? AND id > ?
                AND (? = 1 OR visibility IN ('public', 'party') OR visibility = ?)
                ORDER BY id LIMIT ?""",
                (
                    auth.game_id, after, privileged,
                    f"player:{auth.member_id}", limit + 1,
                ),
            ).fetchall()
            head = int(
                db.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM events WHERE game_id = ?",
                    (auth.game_id,),
                ).fetchone()[0]
            )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        # At the end of the visible stream, move to the aggregate head so hidden
        # events are not scanned again while future public events remain discoverable.
        next_cursor = page_rows[-1]["id"] if has_more else head
        return {
            "events": [self._event_result(row) for row in page_rows],
            "next_cursor": max(after, next_cursor),
            "has_more": has_more,
        }

    def create_request(self, auth: AuthContext, request_type: str, payload: dict) -> dict:
        request_id, timestamp = uuid4().hex, now()
        with self.connect() as db:
            db.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL)", (request_id, auth.game_id, auth.member_id, request_type, json.dumps(payload), timestamp))
        return {"id": request_id, "actor_id": auth.member_id, "type": request_type, "payload": payload, "status": "pending", "created_at": timestamp}

    def pending_requests(self, game_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM requests WHERE game_id = ? AND status = 'pending' ORDER BY created_at", (game_id,)).fetchall()
        return [{"id": row["id"], "actor_id": row["actor_id"], "type": row["type"], "payload": json.loads(row["payload_json"]), "status": row["status"]} for row in rows]

    def resolve_request(self, game_id: str, request_id: str, status: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM requests WHERE id = ? AND game_id = ? AND status = 'pending'", (request_id, game_id)).fetchone()
            if row is None:
                raise KeyError("Bekleyen talep bulunamadı.")
            db.execute("UPDATE requests SET status = ?, resolved_at = ? WHERE id = ?", (status, now(), request_id))
        return {"id": row["id"], "actor_id": row["actor_id"], "type": row["type"], "payload": json.loads(row["payload_json"]), "status": status}

    def campaign_portable_export(self, auth: AuthContext) -> dict:
        """Return campaign-scoped data without credentials or storage paths."""
        # Opt-in prevents future operational or credential tables from being
        # exported merely because they happen to contain a scope identifier.
        portable_tables = {
            "campaigns", "campaign_members", "campaign_ruleset_history",
            "games", "members", "events", "requests", "sessions",
            "session_loot", "session_notes", "session_quests",
            "character_action_history", "character_drafts",
            "character_inventory_history", "character_resource_history",
            "character_schema_history", "encounter_drafts",
            "encounter_undo_history", "map_assets", "map_fog_cells",
            "map_fog_state", "map_scenes", "map_tokens", "map_transients",
            "member_dice_preferences",
        }
        sensitive_columns = {
            "code_hash",
            "invite_code",
            "pepper_fingerprint",
            "storage_key",
            "ticket_hash",
            "token",
            "token_hash",
        }
        with self.read_transaction():
            with self.connect() as db:
                game = db.execute(
                    """
                    SELECT games.campaign_id, campaigns.owner_id
                    FROM games
                    JOIN campaigns ON campaigns.id = games.campaign_id
                    WHERE games.id = ?
                    """,
                    (auth.game_id,),
                ).fetchone()
                if game is None:
                    raise KeyError("Campaign bulunamadi.")
                if auth.member_id != game["owner_id"]:
                    raise PermissionError(
                        "Campaign export yalnizca owner icindir."
                    )
                campaign_id = game["campaign_id"]
                game_ids = [
                    row["id"] for row in db.execute(
                        "SELECT id FROM games WHERE campaign_id = ?",
                        (campaign_id,),
                    ).fetchall()
                ]
                member_ids = [
                    row["id"] for row in db.execute(
                        "SELECT id FROM members WHERE game_id IN ({})".format(
                            ",".join("?" for _ in game_ids)
                        ),
                        game_ids,
                    ).fetchall()
                ] if game_ids else []
                session_ids = [
                    row["id"] for row in db.execute(
                        "SELECT id FROM sessions WHERE campaign_id = ?",
                        (campaign_id,),
                    ).fetchall()
                ]
                table_names = [
                    row["name"] for row in db.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' ORDER BY name
                        """
                    ).fetchall()
                ]
                exported: dict[str, list[dict]] = {}
                for table in table_names:
                    if (
                        table not in portable_tables
                        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table)
                    ):
                        continue
                    columns = [
                        row["name"] for row in db.execute(
                            f'PRAGMA table_info("{table}")'
                        ).fetchall()
                    ]
                    query = f'SELECT * FROM "{table}"'
                    parameters: list[str] = []
                    if table == "campaigns":
                        query += " WHERE id = ?"
                        parameters = [campaign_id]
                    elif "campaign_id" in columns:
                        query += " WHERE campaign_id = ?"
                        parameters = [campaign_id]
                    elif "game_id" in columns and game_ids:
                        query += " WHERE game_id IN ({})".format(
                            ",".join("?" for _ in game_ids)
                        )
                        parameters = game_ids
                    elif table == "members" and game_ids:
                        query += " WHERE game_id IN ({})".format(
                            ",".join("?" for _ in game_ids)
                        )
                        parameters = game_ids
                    elif "session_id" in columns and session_ids:
                        query += " WHERE session_id IN ({})".format(
                            ",".join("?" for _ in session_ids)
                        )
                        parameters = session_ids
                    elif "member_id" in columns and member_ids:
                        query += " WHERE member_id IN ({})".format(
                            ",".join("?" for _ in member_ids)
                        )
                        parameters = member_ids
                    else:
                        continue
                    rows = db.execute(query, parameters).fetchall()
                    result_rows = []
                    for row in rows:
                        item = {}
                        for column in columns:
                            if column in sensitive_columns:
                                continue
                            value = row[column]
                            if column.endswith("_json") and isinstance(value, str):
                                try:
                                    value = json.loads(value)
                                except json.JSONDecodeError as error:
                                    raise RuntimeError(
                                        f"{table}.{column} JSON gecersiz."
                                    ) from error
                            item[column] = _redact_export_value(
                                value, sensitive_columns
                            )
                        result_rows.append(item)
                    if result_rows:
                        exported[table] = result_rows
                schema_version = int(
                    db.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0]
                )
        return {
            "format": "tetsu-campaign-export",
            "format_version": 1,
            "schema_version": schema_version,
            "campaign_id": campaign_id,
            "exported_at": now(),
            "tables": exported,
        }

    def delete_owned_campaign(
        self, auth: AuthContext, confirmation: str
    ) -> dict:
        with self.transaction():
            with self.connect() as db:
                game = db.execute(
                    """
                    SELECT games.id, games.campaign_id, campaigns.name,
                           campaigns.owner_id
                    FROM games
                    JOIN campaigns ON campaigns.id = games.campaign_id
                    WHERE games.id = ?
                    """,
                    (auth.game_id,),
                ).fetchone()
                if game is None:
                    raise KeyError("Campaign bulunamadi.")
                if game["owner_id"] != auth.member_id:
                    raise PermissionError(
                        "Campaign silme yalnizca owner icindir."
                    )
                expected = f"{game['campaign_id']}:{game['name']}"
                if not hmac.compare_digest(
                    confirmation.encode("utf-8"),
                    expected.encode("utf-8"),
                ):
                    raise ValueError(
                        "Silme confirmation campaign_id:name olmali."
                    )
                member_connections = [
                    {"game_id": row["game_id"], "member_id": row["id"]}
                    for row in db.execute(
                        """
                        SELECT members.id, members.game_id
                        FROM members
                        JOIN games ON games.id = members.game_id
                        WHERE games.campaign_id = ?
                        """,
                        (game["campaign_id"],),
                    ).fetchall()
                ]
                storage_keys = [
                    row["storage_key"] for row in db.execute(
                        """
                        SELECT DISTINCT storage_key FROM map_assets
                        WHERE campaign_id = ?
                        """,
                        (game["campaign_id"],),
                    ).fetchall()
                ]
                db.execute(
                    "DELETE FROM games WHERE campaign_id = ?",
                    (game["campaign_id"],),
                )
                cursor = db.execute(
                    "DELETE FROM campaigns WHERE id = ?",
                    (game["campaign_id"],),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Campaign silinemedi.")
                orphan_keys = [
                    key for key in storage_keys
                    if db.execute(
                        "SELECT 1 FROM map_assets WHERE storage_key = ? LIMIT 1",
                        (key,),
                    ).fetchone() is None
                ]
        return {
            "campaign_id": game["campaign_id"],
            "member_connections": member_connections,
            "orphan_storage_keys": orphan_keys,
        }
