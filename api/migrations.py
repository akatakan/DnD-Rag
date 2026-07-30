import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

MigrationAction = Callable[[sqlite3.Connection], None]
Migration = tuple[int, str, MigrationAction]


def _execute_script_atomic(db: sqlite3.Connection, script: str) -> None:
    """Execute a SQL script without sqlite3.executescript's implicit COMMIT."""
    statement: list[str] = []
    for character in script:
        statement.append(character)
        if (
            character == ";"
            and sqlite3.complete_statement("".join(statement))
        ):
            sql = "".join(statement).strip()
            if sql:
                db.execute(sql)
            statement.clear()
    remainder = "".join(statement)
    if remainder.strip():
        try:
            # sqlite accepts comment-only tails and a final statement without
            # a semicolon; it still rejects genuinely incomplete SQL.
            db.execute(remainder)
        except sqlite3.DatabaseError as error:
            raise RuntimeError(
                "Migration SQL script'i tamamlanmamis."
            ) from error


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def _migration_001_initial_multiplayer_schema(db: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            invite_code TEXT UNIQUE NOT NULL,
            dm_mode TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS members (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            character_id TEXT,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            visibility TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            actor_id TEXT NOT NULL,
            type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_events_game ON events(game_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_members_token ON members(token)",
    )
    for statement in statements:
        db.execute(statement)


def _migration_002_dm_handover(db: sqlite3.Connection) -> None:
    game_columns = _columns(db, "games")
    additions = (
        ("owner_id", "ALTER TABLE games ADD COLUMN owner_id TEXT"),
        ("active_dm_id", "ALTER TABLE games ADD COLUMN active_dm_id TEXT"),
        (
            "fallback_dm_mode",
            "ALTER TABLE games ADD COLUMN fallback_dm_mode TEXT NOT NULL DEFAULT 'assisted'",
        ),
        (
            "handover_json",
            "ALTER TABLE games ADD COLUMN handover_json TEXT NOT NULL DEFAULT '{}'",
        ),
    )
    for column, statement in additions:
        if column not in game_columns:
            db.execute(statement)

    db.execute(
        """
        UPDATE games SET
            owner_id = COALESCE(
                owner_id,
                (
                    SELECT id FROM members
                    WHERE members.game_id = games.id AND role = 'dm'
                    ORDER BY created_at LIMIT 1
                )
            ),
            active_dm_id = COALESCE(
                active_dm_id,
                owner_id,
                (
                    SELECT id FROM members
                    WHERE members.game_id = games.id AND role = 'dm'
                    ORDER BY created_at LIMIT 1
                )
            )
        """
    )


def _migration_003_campaign_sessions(db: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('draft', 'active', 'archived')),
            ruleset_version TEXT NOT NULL DEFAULT 'srd-5.2',
            language TEXT NOT NULL DEFAULT 'tr',
            play_style TEXT NOT NULL DEFAULT 'theater',
            public_notes TEXT NOT NULL DEFAULT '',
            settings_json TEXT NOT NULL DEFAULT '{}',
            settings_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            number INTEGER NOT NULL CHECK (number > 0),
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'preparing'
                CHECK (status IN ('preparing', 'live', 'paused', 'completed')),
            scheduled_at TEXT,
            started_at TEXT,
            ended_at TEXT,
            summary_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (campaign_id, number)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS campaign_members (
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            readiness_status TEXT NOT NULL DEFAULT 'not_ready'
                CHECK (readiness_status IN ('not_ready', 'ready')),
            joined_at TEXT NOT NULL,
            PRIMARY KEY (campaign_id, member_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_sessions_campaign ON sessions(campaign_id, number)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_members_member ON campaign_members(member_id)",
    )
    for statement in statements:
        db.execute(statement)

    game_columns = _columns(db, "games")
    if "campaign_id" not in game_columns:
        db.execute("ALTER TABLE games ADD COLUMN campaign_id TEXT")
    if "active_session_id" not in game_columns:
        db.execute("ALTER TABLE games ADD COLUMN active_session_id TEXT")

    # A multiplayer game represented both campaign and current play session before v3.
    # Keep its id as the campaign id so existing links remain stable.
    db.execute(
        """
        INSERT OR IGNORE INTO campaigns (
            id, name, owner_id, status, ruleset_version, language, play_style,
            public_notes, settings_json, settings_version, created_at, updated_at
        )
        SELECT
            id, name, COALESCE(owner_id, active_dm_id, ''), 'active',
            'srd-5.2', 'tr', 'theater', '', '{}', 1, created_at, updated_at
        FROM games
        """
    )
    db.execute(
        """
        INSERT OR IGNORE INTO sessions (
            id, campaign_id, number, title, status, scheduled_at, started_at,
            ended_at, summary_json, created_at, updated_at
        )
        SELECT
            'session-' || id || '-1', id, 1, 'Session 1', 'live', NULL,
            created_at, NULL, '{}', created_at, updated_at
        FROM games
        """
    )
    db.execute(
        """
        INSERT OR IGNORE INTO campaign_members (
            campaign_id, member_id, readiness_status, joined_at
        )
        SELECT game_id, id, 'not_ready', created_at FROM members
        """
    )
    db.execute(
        """
        UPDATE games SET
            campaign_id = COALESCE(campaign_id, id),
            active_session_id = COALESCE(active_session_id, 'session-' || id || '-1')
        """
    )


def _migration_004_revision_idempotency(db: sqlite3.Connection) -> None:
    if "state_revision" not in _columns(db, "games"):
        db.execute(
            "ALTER TABLE games ADD COLUMN state_revision INTEGER NOT NULL DEFAULT 0"
        )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS command_receipts (
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            actor_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            client_action_id TEXT NOT NULL,
            command_type TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (game_id, actor_id, client_action_id)
        )
        """
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_command_receipts_created
        ON command_receipts(game_id, created_at)"""
    )


def _migration_005_public_auth(db: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS auth_tokens (
            id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            token_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            rotated_from_id TEXT REFERENCES auth_tokens(id),
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS game_invites (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            code_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            max_uses INTEGER NOT NULL CHECK (max_uses > 0),
            use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count >= 0),
            created_by TEXT REFERENCES members(id),
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS websocket_tickets (
            id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            ticket_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS security_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            actor_id TEXT,
            action TEXT NOT NULL,
            target_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_auth_tokens_member ON auth_tokens(member_id, expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_game_invites_game ON game_invites(game_id, expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_ws_tickets_member ON websocket_tickets(member_id, expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_security_audit_game ON security_audit_events(game_id, id)",
    )
    for statement in statements:
        db.execute(statement)


def _migration_006_bind_websocket_tickets(db: sqlite3.Connection) -> None:
    if "auth_token_id" not in _columns(db, "websocket_tickets"):
        db.execute(
            """ALTER TABLE websocket_tickets
            ADD COLUMN auth_token_id TEXT REFERENCES auth_tokens(id)"""
        )
    # Tickets live for only 60 seconds and cannot be safely associated with the
    # token that created them retroactively.
    db.execute("DELETE FROM websocket_tickets")


def _migration_007_auth_configuration(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_configuration (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            pepper_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _migration_008_srd_521_ruleset(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_ruleset_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            from_version TEXT NOT NULL,
            to_version TEXT NOT NULL,
            reason TEXT NOT NULL,
            changed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO campaign_ruleset_history (
            campaign_id, from_version, to_version, reason, changed_at
        )
        SELECT id, ruleset_version, 'srd-5.2.1', 'migration:8',
               strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        FROM campaigns
        WHERE ruleset_version = 'srd-5.2'
        """
    )
    db.execute(
        """
        UPDATE campaigns
        SET ruleset_version = 'srd-5.2.1',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE ruleset_version = 'srd-5.2'
        """
    )


def _migration_009_character_aggregate(db: sqlite3.Connection) -> None:
    from api.character_engine import CHARACTER_SCHEMA_VERSION, CharacterEngine

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS character_schema_history (
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            character_id TEXT NOT NULL,
            from_version INTEGER NOT NULL,
            to_version INTEGER NOT NULL,
            migrated_at TEXT NOT NULL,
            PRIMARY KEY (game_id, character_id, to_version)
        )
        """
    )
    engine = CharacterEngine()
    rows = db.execute(
        """
        SELECT games.id, games.state_json, campaigns.ruleset_version
        FROM games
        JOIN campaigns ON campaigns.id = games.campaign_id
        """
    ).fetchall()
    for row in rows:
        state = json.loads(row["state_json"])
        changed = False
        for character_id, character in list(state.get("characters", {}).items()):
            from_version = int(character.get("schema_version", 1))
            migrated = engine.migrate_legacy(
                character, ruleset_version=row["ruleset_version"]
            )
            if migrated != character:
                state["characters"][character_id] = migrated
                changed = True
            if from_version < CHARACTER_SCHEMA_VERSION:
                db.execute(
                    """
                    INSERT OR IGNORE INTO character_schema_history (
                        game_id, character_id, from_version, to_version, migrated_at
                    ) VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (
                        row["id"],
                        character_id,
                        from_version,
                        CHARACTER_SCHEMA_VERSION,
                    ),
                )
        if changed:
            db.execute(
                """
                UPDATE games
                SET state_json = ?, state_revision = state_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (json.dumps(state, ensure_ascii=False), row["id"]),
            )


def _migration_010_character_resources(db: sqlite3.Connection) -> None:
    from api.resource_engine import (
        RESOURCE_SCHEMA_VERSION,
        ResourceEngine,
        ResourceValidationError,
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS character_resource_history (
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            character_id TEXT NOT NULL,
            from_version INTEGER NOT NULL,
            to_version INTEGER NOT NULL,
            migrated_at TEXT NOT NULL,
            PRIMARY KEY (game_id, character_id, to_version)
        )
        """
    )
    engine = ResourceEngine()
    rows = db.execute("SELECT id, state_json FROM games").fetchall()
    for row in rows:
        state = json.loads(row["state_json"])
        changed = "turn_serial" not in state
        state.setdefault(
            "turn_serial", 1 if state.get("encounter_status") == "active" else 0
        )
        for character_id, character in list(state.get("characters", {}).items()):
            current = character.get("resource_state", {}).get("schema_version", 0)
            if (
                not isinstance(current, int)
                or isinstance(current, bool)
                or current < 0
            ):
                raise RuntimeError("Character resource schema version gecersiz.")
            if current > RESOURCE_SCHEMA_VERSION:
                raise RuntimeError(
                    "Character resource schema uygulamadan daha yeni; "
                    "veri kaybini onlemek icin migration durduruldu."
                )
            try:
                if current == 0:
                    migrated = engine.initialize(character)
                else:
                    migrated = json.loads(json.dumps(character))
                    if current == 1:
                        saves = migrated["resource_state"]["death_saves"]
                        previous_roll = saves.pop("last_rolled_round", None)
                        saves["last_rolled_turn"] = (
                            state["turn_serial"]
                            if previous_roll is not None
                            and state.get("encounter_status") == "active"
                            else None
                        )
                        migrated["resource_state"][
                            "schema_version"
                        ] = RESOURCE_SCHEMA_VERSION
                    migrated = engine.sync(migrated)
            except ResourceValidationError as error:
                raise RuntimeError(
                    f"Character resource state gecersiz: {character_id}"
                ) from error
            if migrated != character:
                state["characters"][character_id] = migrated
                changed = True
            if current < RESOURCE_SCHEMA_VERSION:
                db.execute(
                    """
                    INSERT OR IGNORE INTO character_resource_history (
                        game_id, character_id, from_version, to_version, migrated_at
                    ) VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (row["id"], character_id, current, RESOURCE_SCHEMA_VERSION),
                )
        if changed:
            db.execute(
                """
                UPDATE games
                SET state_json = ?, state_revision = state_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (json.dumps(state, ensure_ascii=False), row["id"]),
            )


def _migration_011_resource_turn_serial(db: sqlite3.Connection) -> None:
    # Re-run the idempotent resource upgrader for databases that had already
    # applied the original v10 resource schema during development.
    _migration_010_character_resources(db)


def _migration_012_turn_action_ledger(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT id, state_json FROM games").fetchall()
    for row in rows:
        state = json.loads(row["state_json"])
        if "turn_actions" in state:
            continue
        state["turn_actions"] = {}
        db.execute(
            """
            UPDATE games
            SET state_json = ?, state_revision = state_revision + 1,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (json.dumps(state, ensure_ascii=False), row["id"]),
        )


def _migration_013_character_inventory(db: sqlite3.Connection) -> None:
    from api.character_engine import CharacterEngine, CharacterValidationError
    from api.inventory_engine import (
        INVENTORY_SCHEMA_VERSION,
        InventoryEngine,
        InventoryValidationError,
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS character_inventory_history (
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            character_id TEXT NOT NULL,
            from_version INTEGER NOT NULL,
            to_version INTEGER NOT NULL,
            migrated_at TEXT NOT NULL,
            PRIMARY KEY (game_id, character_id, to_version)
        )
        """
    )
    engine = InventoryEngine()
    character_engine = CharacterEngine(engine.catalog)
    rows = db.execute("SELECT id, state_json FROM games").fetchall()
    for row in rows:
        state = json.loads(row["state_json"])
        changed = False
        for character_id, character in list(state.get("characters", {}).items()):
            inventory_state = character.get("inventory_state")
            if inventory_state is None:
                current = 0
            elif not isinstance(inventory_state, dict):
                raise RuntimeError("Character inventory state gecersiz.")
            else:
                current = inventory_state.get("schema_version", 0)
            if (
                not isinstance(current, int)
                or isinstance(current, bool)
                or current < 0
            ):
                raise RuntimeError("Character inventory schema version gecersiz.")
            if current > INVENTORY_SCHEMA_VERSION:
                raise RuntimeError(
                    "Character inventory schema uygulamadan daha yeni; "
                    "veri kaybini onlemek icin migration durduruldu."
                )
            try:
                migrated = (
                    engine.sync(character)
                    if current == INVENTORY_SCHEMA_VERSION
                    else engine.initialize(character)
                )
                migrated = character_engine.recalculate(migrated)
            except (InventoryValidationError, CharacterValidationError) as error:
                raise RuntimeError(
                    f"Character inventory state gecersiz: {character_id}"
                ) from error
            if migrated != character:
                state["characters"][character_id] = migrated
                changed = True
            if current < INVENTORY_SCHEMA_VERSION:
                db.execute(
                    """
                    INSERT OR IGNORE INTO character_inventory_history (
                        game_id, character_id, from_version, to_version, migrated_at
                    ) VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (
                        row["id"],
                        character_id,
                        current,
                        INVENTORY_SCHEMA_VERSION,
                    ),
                )
        if changed:
            db.execute(
                """
                UPDATE games
                SET state_json = ?, state_revision = state_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (json.dumps(state, ensure_ascii=False), row["id"]),
            )


def _migration_014_character_actions(db: sqlite3.Connection) -> None:
    from api.action_engine import (
        ACTION_SCHEMA_VERSION,
        ActionEngine,
        ActionValidationError,
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS character_action_history (
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            character_id TEXT NOT NULL,
            from_version INTEGER NOT NULL,
            to_version INTEGER NOT NULL,
            migrated_at TEXT NOT NULL,
            PRIMARY KEY (game_id, character_id, to_version)
        )
        """
    )
    engine = ActionEngine()
    rows = db.execute("SELECT id, state_json FROM games").fetchall()
    for row in rows:
        state = json.loads(row["state_json"])
        changed = False
        for character_id, character in list(state.get("characters", {}).items()):
            action_state = character.get("action_state")
            if action_state is None:
                current = 0
            elif not isinstance(action_state, dict):
                raise RuntimeError("Character action state gecersiz.")
            else:
                current = action_state.get("schema_version", 0)
            if (
                not isinstance(current, int)
                or isinstance(current, bool)
                or current < 0
            ):
                raise RuntimeError("Character action schema version gecersiz.")
            if current > ACTION_SCHEMA_VERSION:
                raise RuntimeError(
                    "Character action schema uygulamadan daha yeni; "
                    "veri kaybini onlemek icin migration durduruldu."
                )
            try:
                migrated = (
                    engine.sync(character)
                    if current == ACTION_SCHEMA_VERSION
                    else engine.initialize(character)
                )
            except ActionValidationError as error:
                raise RuntimeError(
                    f"Character action state gecersiz: {character_id}"
                ) from error
            if migrated != character:
                state["characters"][character_id] = migrated
                changed = True
            if current < ACTION_SCHEMA_VERSION:
                db.execute(
                    """
                    INSERT OR IGNORE INTO character_action_history (
                        game_id, character_id, from_version, to_version, migrated_at
                    ) VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (row["id"], character_id, current, ACTION_SCHEMA_VERSION),
                )
        if changed:
            db.execute(
                """
                UPDATE games
                SET state_json = ?, state_revision = state_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (json.dumps(state, ensure_ascii=False), row["id"]),
            )


def _migration_015_character_drafts(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS character_drafts (
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            character_id TEXT NOT NULL,
            owner_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            schema_version INTEGER NOT NULL CHECK (schema_version = 1),
            draft_json TEXT NOT NULL,
            current_step TEXT NOT NULL CHECK (
                current_step IN (
                    'basics', 'abilities', 'class', 'species', 'background',
                    'proficiencies', 'equipment', 'spells', 'review'
                )
            ),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'published')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT,
            PRIMARY KEY (game_id, character_id)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_character_drafts_owner
        ON character_drafts (game_id, owner_id, status)
        """
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS character_drafts_owner_game_insert
        BEFORE INSERT ON character_drafts
        WHEN NOT EXISTS (
            SELECT 1 FROM members
            WHERE id = NEW.owner_id
              AND game_id = NEW.game_id
              AND character_id = NEW.character_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'draft owner/game/character mismatch');
        END
        """
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS character_drafts_owner_game_update
        BEFORE UPDATE OF game_id, character_id, owner_id ON character_drafts
        WHEN NOT EXISTS (
            SELECT 1 FROM members
            WHERE id = NEW.owner_id
              AND game_id = NEW.game_id
              AND character_id = NEW.character_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'draft owner/game/character mismatch');
        END
        """
    )

    from api.character_draft_engine import (
        DRAFT_STEPS,
        CharacterDraftEngine,
        CharacterDraftValidationError,
    )
    from api.character_engine import CharacterEngine

    engine = CharacterDraftEngine(CharacterEngine())
    rows = db.execute(
        """
        SELECT schema_version, draft_json, current_step, revision, status
        FROM character_drafts
        """
    ).fetchall()
    owner_mismatch = db.execute(
        """
        SELECT 1
        FROM character_drafts AS drafts
        LEFT JOIN members
          ON members.id = drafts.owner_id
         AND members.game_id = drafts.game_id
         AND members.character_id = drafts.character_id
        WHERE members.id IS NULL
        LIMIT 1
        """
    ).fetchone()
    if owner_mismatch is not None:
        raise RuntimeError(
            "Persisted character draft owner/game/character eslesmesi gecersiz."
        )
    for row in rows:
        try:
            data = json.loads(row["draft_json"])
            if data.get("schema_version") == 1:
                engine.validate_shape(engine.migrate_v1(data))
            else:
                engine.validate_shape(data)
        except (json.JSONDecodeError, CharacterDraftValidationError) as error:
            raise RuntimeError("Persisted character draft gecersiz.") from error
        if (
            row["schema_version"] != data["schema_version"]
            or row["schema_version"] not in {1, 2}
            or row["current_step"] not in DRAFT_STEPS
            or not isinstance(row["revision"], int)
            or row["revision"] < 1
            or row["status"] not in {"active", "published"}
        ):
            raise RuntimeError("Persisted character draft metadata gecersiz.")


def _migration_016_session_zero(db: sqlite3.Connection) -> None:
    columns = _columns(db, "campaign_members")
    additions = (
        (
            "readiness_version",
            "ALTER TABLE campaign_members ADD COLUMN readiness_version "
            "INTEGER NOT NULL DEFAULT 1 CHECK (readiness_version > 0)",
        ),
        (
            "consent_status",
            "ALTER TABLE campaign_members ADD COLUMN consent_status "
            "TEXT NOT NULL DEFAULT 'pending' "
            "CHECK (consent_status IN ('pending', 'accepted', 'declined'))",
        ),
        (
            "safety_preferences_json",
            "ALTER TABLE campaign_members ADD COLUMN safety_preferences_json "
            "TEXT NOT NULL DEFAULT '{}'",
        ),
        (
            "updated_at",
            "ALTER TABLE campaign_members ADD COLUMN updated_at TEXT",
        ),
    )
    for column, statement in additions:
        if column not in columns:
            db.execute(statement)
    db.execute(
        "UPDATE campaign_members SET updated_at = COALESCE(updated_at, joined_at)"
    )
    rows = db.execute("SELECT id, settings_json FROM campaigns").fetchall()
    for row in rows:
        try:
            settings = json.loads(row["settings_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Campaign settings JSON gecersiz.") from error
        if not isinstance(settings, dict):
            raise RuntimeError("Campaign settings obje olmali.")
        version = settings.get("schema_version")
        if version not in {None, 1} or isinstance(version, bool):
            raise RuntimeError(
                "Campaign settings schema uygulamadan daha yeni veya gecersiz."
            )
        normalized = {
            "schema_version": 1,
            "house_rules": settings.get("house_rules", []),
            "safety_tools": settings.get(
                "safety_tools", ["x_card", "lines_veils", "open_door"]
            ),
            "session_zero_agenda": settings.get("session_zero_agenda", []),
        }
        if not all(
            isinstance(normalized[key], list)
            for key in ("house_rules", "safety_tools", "session_zero_agenda")
        ):
            raise RuntimeError("Campaign settings listeleri gecersiz.")
        house_rules = normalized["house_rules"]
        if len(house_rules) > 50 or any(
            not isinstance(rule, dict)
            or not isinstance(rule.get("id"), str)
            or not 1 <= len(rule["id"]) <= 64
            or not isinstance(rule.get("title"), str)
            or not 1 <= len(rule["title"]) <= 120
            or not isinstance(rule.get("description", ""), str)
            or len(rule.get("description", "")) > 1000
            or not isinstance(rule.get("enabled", True), bool)
            for rule in house_rules
        ):
            raise RuntimeError("Campaign house rules gecersiz.")
        rule_ids = [rule["id"] for rule in house_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise RuntimeError("Campaign house rule id degerleri tekrar ediyor.")
        allowed_tools = {
            "x_card", "lines_veils", "open_door", "stars_wishes"
        }
        if (
            len(normalized["safety_tools"]) > 4
            or any(
                not isinstance(tool, str) or tool not in allowed_tools
                for tool in normalized["safety_tools"]
            )
            or len(normalized["safety_tools"])
            != len(set(normalized["safety_tools"]))
        ):
            raise RuntimeError("Campaign safety tools gecersiz.")
        if (
            len(normalized["session_zero_agenda"]) > 30
            or any(
                not isinstance(item, str) or not 1 <= len(item) <= 240
                for item in normalized["session_zero_agenda"]
            )
        ):
            raise RuntimeError("Session Zero agenda gecersiz.")
        db.execute(
            "UPDATE campaigns SET settings_json = ? WHERE id = ?",
            (json.dumps(normalized, ensure_ascii=False), row["id"]),
        )
    preference_rows = db.execute(
        "SELECT safety_preferences_json FROM campaign_members"
    ).fetchall()
    for row in preference_rows:
        try:
            preferences = json.loads(row["safety_preferences_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Session Zero safety preferences JSON gecersiz."
            ) from error
        if not isinstance(preferences, dict) or set(preferences) - {
            "lines", "veils", "notes"
        }:
            raise RuntimeError("Session Zero safety preferences gecersiz.")
        for field in ("lines", "veils"):
            values = preferences.get(field, [])
            if (
                not isinstance(values, list)
                or len(values) > 50
                or any(
                    not isinstance(value, str) or not 1 <= len(value) <= 240
                    for value in values
                )
            ):
                raise RuntimeError(
                    "Session Zero safety preferences listesi gecersiz."
                )
        notes = preferences.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 2000:
            raise RuntimeError(
                "Session Zero safety preferences notu gecersiz."
            )


def _migration_017_session_workspace(db: sqlite3.Connection) -> None:
    _execute_script_atomic(
        db,
        """
        CREATE TABLE IF NOT EXISTS session_notes (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            author_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            visibility TEXT NOT NULL CHECK (
                visibility IN ('party', 'dm_only')
                OR visibility LIKE 'player:%'
            ),
            content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 4000),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_session_notes_session
        ON session_notes (session_id, created_at);

        CREATE TABLE IF NOT EXISTS session_loot (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
            quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 1000000),
            status TEXT NOT NULL DEFAULT 'available'
                CHECK (status IN ('available', 'claimed')),
            claimant_id TEXT REFERENCES members(id) ON DELETE SET NULL,
            created_by TEXT NOT NULL REFERENCES members(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (status = 'available' AND claimant_id IS NULL)
                OR (status = 'claimed' AND claimant_id IS NOT NULL)
            )
        );
        CREATE INDEX IF NOT EXISTS idx_session_loot_session
        ON session_loot (session_id, status);

        CREATE TABLE IF NOT EXISTS session_quests (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 160),
            description TEXT NOT NULL CHECK (length(description) <= 2000),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'completed', 'failed')),
            created_by TEXT NOT NULL REFERENCES members(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_session_quests_session
        ON session_quests (session_id, status);
        """
    )
    rows = db.execute("SELECT id, summary_json FROM sessions").fetchall()
    for row in rows:
        try:
            summary = json.loads(row["summary_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Session summary JSON gecersiz.") from error
        if not isinstance(summary, dict):
            raise RuntimeError("Session summary obje olmali.")
        if not summary:
            continue
        if set(summary) != {
            "schema_version", "title", "highlights", "next_steps", "published"
        }:
            raise RuntimeError("Session summary alanlari gecersiz.")
        if (
            summary.get("schema_version") != 1
            or isinstance(summary.get("schema_version"), bool)
            or not isinstance(summary.get("title"), str)
            or len(summary["title"]) > 160
            or not isinstance(summary.get("published"), bool)
        ):
            raise RuntimeError("Session summary metadata gecersiz.")
        for field in ("highlights", "next_steps"):
            values = summary.get(field)
            if (
                not isinstance(values, list)
                or len(values) > 50
                or any(
                    not isinstance(value, str) or not 1 <= len(value) <= 500
                    for value in values
                )
            ):
                raise RuntimeError(f"Session summary {field} gecersiz.")


def _migration_018_encounter_library(db: sqlite3.Connection) -> None:
    _execute_script_atomic(
        db,
        """
        CREATE TABLE IF NOT EXISTS encounter_drafts (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL
                REFERENCES campaigns(id) ON DELETE CASCADE,
            created_by TEXT NOT NULL REFERENCES members(id),
            schema_version INTEGER NOT NULL CHECK (schema_version = 1),
            name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
            description TEXT NOT NULL CHECK (length(description) <= 2000),
            draft_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_encounter_drafts_campaign
        ON encounter_drafts (campaign_id, updated_at DESC);

        CREATE TRIGGER IF NOT EXISTS encounter_drafts_member_insert
        BEFORE INSERT ON encounter_drafts
        WHEN NOT EXISTS (
            SELECT 1 FROM members
            JOIN games ON games.id = members.game_id
            WHERE members.id = NEW.created_by
              AND games.campaign_id = NEW.campaign_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'encounter creator/campaign mismatch');
        END;

        CREATE TRIGGER IF NOT EXISTS encounter_drafts_member_update
        BEFORE UPDATE OF campaign_id, created_by ON encounter_drafts
        WHEN NOT EXISTS (
            SELECT 1 FROM members
            JOIN games ON games.id = members.game_id
            WHERE members.id = NEW.created_by
              AND games.campaign_id = NEW.campaign_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'encounter creator/campaign mismatch');
        END;
        """
    )
    rows = db.execute("SELECT id, campaign_id, state_json FROM games").fetchall()
    for row in rows:
        try:
            state = json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Encounter migration game state JSON gecersiz.") from error
        if not isinstance(state, dict):
            raise RuntimeError("Encounter migration game state obje olmali.")
        status = state.get("encounter_status", "idle")
        if status not in {"idle", "active", "paused", "completed"}:
            raise RuntimeError("Encounter migration status gecersiz.")
        changed = False
        for key in ("active_encounter_id", "active_encounter_revision"):
            if key not in state:
                state[key] = None
                changed = True
        active_encounter_id = state["active_encounter_id"]
        active_encounter_revision = state["active_encounter_revision"]
        if (active_encounter_id is None) != (active_encounter_revision is None):
            raise RuntimeError("Encounter migration active reference eksik.")
        if active_encounter_id is not None:
            if (
                not isinstance(active_encounter_id, str)
                or not 8 <= len(active_encounter_id) <= 64
                or isinstance(active_encounter_revision, bool)
                or not isinstance(active_encounter_revision, int)
                or active_encounter_revision < 1
            ):
                raise RuntimeError("Encounter migration active reference gecersiz.")
            referenced = db.execute(
                """
                SELECT revision FROM encounter_drafts
                WHERE id = ? AND campaign_id = ?
                """,
                (active_encounter_id, row["campaign_id"]),
            ).fetchone()
            if referenced is None or referenced["revision"] < active_encounter_revision:
                raise RuntimeError("Encounter migration active reference gecersiz.")
        if changed:
            db.execute(
                """
                UPDATE games SET state_json = ?,
                    state_revision = state_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (json.dumps(state, ensure_ascii=False), row["id"]),
            )
    from api.encounter_engine import (
        ENCOUNTER_SCHEMA_VERSION,
        EncounterEngine,
        EncounterValidationError,
    )

    engine = EncounterEngine()
    draft_rows = db.execute(
        """
        SELECT schema_version, name, description, draft_json, revision
        FROM encounter_drafts
        """
    ).fetchall()
    for row in draft_rows:
        try:
            draft = json.loads(row["draft_json"])
            engine.validate(draft)
        except (TypeError, json.JSONDecodeError, EncounterValidationError) as error:
            raise RuntimeError("Persisted encounter draft gecersiz.") from error
        if (
            row["schema_version"] != ENCOUNTER_SCHEMA_VERSION
            or row["schema_version"] != draft["schema_version"]
            or row["name"] != draft["name"]
            or row["description"] != draft["description"]
            or not isinstance(row["revision"], int)
            or row["revision"] < 1
        ):
            raise RuntimeError("Persisted encounter draft metadata gecersiz.")


def _migration_019_advanced_live_encounter(db: sqlite3.Connection) -> None:
    _execute_script_atomic(
        db,
        """
        CREATE TABLE IF NOT EXISTS encounter_undo_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            actor_id TEXT NOT NULL REFERENCES members(id),
            command_type TEXT NOT NULL CHECK (
                length(command_type) BETWEEN 1 AND 80
            ),
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_encounter_undo_game
        ON encounter_undo_history (game_id, id DESC);
        CREATE TRIGGER IF NOT EXISTS encounter_undo_actor_insert
        BEFORE INSERT ON encounter_undo_history
        WHEN NOT EXISTS (
            SELECT 1 FROM members
            WHERE id = NEW.actor_id AND game_id = NEW.game_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'encounter undo actor/game mismatch');
        END;
        CREATE TRIGGER IF NOT EXISTS encounter_undo_actor_update
        BEFORE UPDATE OF game_id, actor_id ON encounter_undo_history
        WHEN NOT EXISTS (
            SELECT 1 FROM members
            WHERE id = NEW.actor_id AND game_id = NEW.game_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'encounter undo actor/game mismatch');
        END;
        """
    )
    rows = db.execute("SELECT id, state_json FROM games").fetchall()
    for row in rows:
        try:
            state = json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Advanced encounter migration state JSON gecersiz."
            ) from error
        combatants = state.get("combatants")
        if not isinstance(combatants, list) or len(combatants) > 200:
            raise RuntimeError(
                "Advanced encounter migration combatant listesi gecersiz."
            )
        changed = False
        seen: set[str] = set()
        for combatant in combatants:
            if (
                not isinstance(combatant, dict)
                or not isinstance(combatant.get("id"), str)
                or not 1 <= len(combatant["id"]) <= 64
                or combatant["id"] in seen
            ):
                raise RuntimeError(
                    "Advanced encounter migration combatant gecersiz."
                )
            seen.add(combatant["id"])
            if "tie_breaker" not in combatant:
                combatant["tie_breaker"] = 0
                changed = True
            value = combatant["tie_breaker"]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not -100 <= value <= 100
            ):
                raise RuntimeError(
                    "Advanced encounter migration tie breaker gecersiz."
                )
        if changed:
            db.execute(
                """
                UPDATE games SET state_json = ?,
                    state_revision = state_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (json.dumps(state, ensure_ascii=False), row["id"]),
            )


def _migration_020_typed_roll_events(db: sqlite3.Connection) -> None:
    event_columns = _columns(db, "events")
    if "typed_intent_id" not in event_columns:
        db.execute("ALTER TABLE events ADD COLUMN typed_intent_id TEXT")
    if "intent_schema_version" not in event_columns:
        db.execute("ALTER TABLE events ADD COLUMN intent_schema_version INTEGER")
    rows = db.execute(
        """
        SELECT id, type, payload_json, typed_intent_id, intent_schema_version
        FROM events
        """
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Typed event payload JSON gecersiz.") from error
        intent = payload.get("intent") if isinstance(payload, dict) else None
        if intent is None:
            if (
                row["type"] == "typed_roll_resolved"
                or row["typed_intent_id"] is not None
                or row["intent_schema_version"] is not None
            ):
                raise RuntimeError("Typed event metadata eksik.")
            continue
        if (
            not isinstance(intent, dict)
            or not isinstance(intent.get("intent_id"), str)
            or not 16 <= len(intent["intent_id"]) <= 64
            or not isinstance(intent.get("schema_version"), int)
            or intent.get("schema_version") != 1
            or isinstance(intent.get("schema_version"), bool)
        ):
            raise RuntimeError("Typed event metadata gecersiz.")
        db.execute(
            """
            UPDATE events
            SET typed_intent_id = ?, intent_schema_version = ?
            WHERE id = ?
            """,
            (intent["intent_id"], intent["schema_version"], row["id"]),
        )
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_events_typed_intent
        ON events(game_id, typed_intent_id)
        WHERE typed_intent_id IS NOT NULL
        """
    )
    db.execute("DROP TRIGGER IF EXISTS trg_events_typed_intent_insert")
    db.execute("DROP TRIGGER IF EXISTS trg_events_typed_intent_update")
    db.execute(
        """
        CREATE TRIGGER trg_events_typed_intent_insert
        BEFORE INSERT ON events
        WHEN CASE
            WHEN json_valid(NEW.payload_json) = 0 THEN 1
            WHEN NEW.type = 'typed_roll_resolved'
                OR json_type(NEW.payload_json, '$.intent') IS NOT NULL
                OR NEW.typed_intent_id IS NOT NULL
                OR NEW.intent_schema_version IS NOT NULL
            THEN
                NEW.typed_intent_id IS NULL
                OR NEW.intent_schema_version IS NULL
                OR length(NEW.typed_intent_id) < 16
                OR length(NEW.typed_intent_id) > 64
                OR NEW.intent_schema_version != 1
                OR json_extract(
                    NEW.payload_json, '$.intent.intent_id'
                ) IS NOT NEW.typed_intent_id
                OR json_extract(
                    NEW.payload_json, '$.intent.schema_version'
                ) IS NOT NEW.intent_schema_version
                OR json_type(
                    NEW.payload_json, '$.intent.schema_version'
                ) != 'integer'
                OR NOT EXISTS (
                    SELECT 1 FROM members
                    WHERE id = NEW.actor_id AND game_id = NEW.game_id
                )
            ELSE 0
        END
        BEGIN
            SELECT RAISE(ABORT, 'typed roll event metadata invalid');
        END
        """
    )
    db.execute(
        """
        CREATE TRIGGER trg_events_typed_intent_update
        BEFORE UPDATE OF game_id, type, actor_id, payload_json,
                         typed_intent_id, intent_schema_version ON events
        WHEN CASE
            WHEN json_valid(NEW.payload_json) = 0 THEN 1
            WHEN NEW.type = 'typed_roll_resolved'
                OR json_type(NEW.payload_json, '$.intent') IS NOT NULL
                OR NEW.typed_intent_id IS NOT NULL
                OR NEW.intent_schema_version IS NOT NULL
            THEN
                NEW.typed_intent_id IS NULL
                OR NEW.intent_schema_version IS NULL
                OR length(NEW.typed_intent_id) < 16
                OR length(NEW.typed_intent_id) > 64
                OR NEW.intent_schema_version != 1
                OR json_extract(
                    NEW.payload_json, '$.intent.intent_id'
                ) IS NOT NEW.typed_intent_id
                OR json_extract(
                    NEW.payload_json, '$.intent.schema_version'
                ) IS NOT NEW.intent_schema_version
                OR json_type(
                    NEW.payload_json, '$.intent.schema_version'
                ) != 'integer'
                OR NOT EXISTS (
                    SELECT 1 FROM members
                    WHERE id = NEW.actor_id AND game_id = NEW.game_id
                )
            ELSE 0
        END
        BEGIN
            SELECT RAISE(ABORT, 'typed roll event metadata invalid');
        END
        """
    )


def _migration_021_dice_preferences(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS member_dice_preferences (
            member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            theme TEXT NOT NULL DEFAULT 'crimson'
                CHECK (theme IN ('crimson', 'arcane', 'ivory')),
            sound_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (sound_enabled IN (0, 1)),
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_member_dice_preferences_game
        ON member_dice_preferences(game_id, member_id)
        """
    )
    for operation in ("INSERT", "UPDATE OF member_id, game_id"):
        suffix = "insert" if operation == "INSERT" else "update"
        db.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_dice_preferences_member_{suffix}
            BEFORE {operation} ON member_dice_preferences
            WHEN NOT EXISTS (
                SELECT 1 FROM members
                WHERE id = NEW.member_id AND game_id = NEW.game_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'dice preference member scope invalid');
            END
            """
        )
    db.execute(
        """
        INSERT OR IGNORE INTO member_dice_preferences (
            member_id, game_id, theme, sound_enabled, updated_at
        )
        SELECT id, game_id, 'crimson', 1,
            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        FROM members
        """
    )
    invalid = db.execute(
        """
        SELECT preferences.member_id
        FROM member_dice_preferences AS preferences
        LEFT JOIN members ON members.id = preferences.member_id
        WHERE members.id IS NULL
           OR members.game_id != preferences.game_id
           OR preferences.theme NOT IN ('crimson', 'arcane', 'ivory')
           OR typeof(preferences.sound_enabled) != 'integer'
           OR preferences.sound_enabled NOT IN (0, 1)
           OR typeof(preferences.updated_at) != 'text'
           OR length(preferences.updated_at) < 1
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise RuntimeError("Persisted dice preference metadata gecersiz.")


def _migration_022_map_assets_and_scenes(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS map_assets (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            uploader_id TEXT NOT NULL REFERENCES members(id) ON DELETE RESTRICT,
            original_name TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            content_type TEXT NOT NULL CHECK (
                content_type IN ('image/png', 'image/jpeg')
            ),
            byte_size INTEGER NOT NULL CHECK (
                byte_size > 0 AND byte_size <= 10485760
            ),
            width INTEGER NOT NULL CHECK (width BETWEEN 64 AND 8192),
            height INTEGER NOT NULL CHECK (height BETWEEN 64 AND 8192),
            created_at TEXT NOT NULL,
            UNIQUE (campaign_id, id)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_map_assets_campaign_created
        ON map_assets(campaign_id, created_at DESC)
        """
    )
    for operation in ("INSERT", "UPDATE OF campaign_id, uploader_id"):
        suffix = "insert" if operation == "INSERT" else "update"
        db.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_map_asset_uploader_scope_{suffix}
            BEFORE {operation} ON map_assets
            WHEN NOT EXISTS (
                SELECT 1
                FROM members
                JOIN games ON games.id = members.game_id
                WHERE members.id = NEW.uploader_id
                  AND games.campaign_id = NEW.campaign_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'map asset uploader scope invalid');
            END
            """
        )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS map_scenes (
            game_id TEXT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            asset_id TEXT,
            name TEXT NOT NULL,
            grid_type TEXT NOT NULL DEFAULT 'square'
                CHECK (grid_type IN ('none', 'square', 'hex')),
            grid_size_px INTEGER NOT NULL DEFAULT 70
                CHECK (grid_size_px BETWEEN 10 AND 512),
            distance_per_cell REAL NOT NULL DEFAULT 5
                CHECK (distance_per_cell >= 0.1 AND distance_per_cell <= 1000),
            distance_unit TEXT NOT NULL DEFAULT 'ft'
                CHECK (distance_unit IN ('ft', 'm')),
            viewport_x REAL NOT NULL DEFAULT 0
                CHECK (viewport_x BETWEEN -100000 AND 100000),
            viewport_y REAL NOT NULL DEFAULT 0
                CHECK (viewport_y BETWEEN -100000 AND 100000),
            viewport_zoom REAL NOT NULL DEFAULT 1
                CHECK (viewport_zoom BETWEEN 0.1 AND 8),
            published INTEGER NOT NULL DEFAULT 0
                CHECK (published IN (0, 1)),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            updated_at TEXT NOT NULL,
            CHECK (published = 0 OR asset_id IS NOT NULL),
            FOREIGN KEY (campaign_id, asset_id)
                REFERENCES map_assets(campaign_id, id) ON DELETE RESTRICT
        )
        """
    )
    for operation in ("INSERT", "UPDATE OF game_id, campaign_id"):
        suffix = "insert" if operation == "INSERT" else "update"
        db.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_map_scene_scope_{suffix}
            BEFORE {operation} ON map_scenes
            WHEN NOT EXISTS (
                SELECT 1 FROM games
                WHERE id = NEW.game_id AND campaign_id = NEW.campaign_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'map scene campaign scope invalid');
            END
            """
        )
    for operation in ("INSERT", "UPDATE OF published, asset_id"):
        suffix = "insert" if operation == "INSERT" else "update"
        db.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_map_scene_publish_asset_{suffix}
            BEFORE {operation} ON map_scenes
            WHEN NEW.published = 1 AND NEW.asset_id IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'published map scene requires asset');
            END
            """
        )
    db.execute(
        """
        INSERT OR IGNORE INTO map_scenes (
            game_id, campaign_id, asset_id, name, grid_type, grid_size_px,
            distance_per_cell, distance_unit, viewport_x, viewport_y,
            viewport_zoom, published, revision, updated_at
        )
        SELECT id, campaign_id, NULL, 'Battle Map', 'square', 70,
            5, 'ft', 0, 0, 1, 0, 1,
            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        FROM games
        """
    )
    invalid = db.execute(
        """
        SELECT map_scenes.game_id
        FROM map_scenes
        LEFT JOIN games ON games.id = map_scenes.game_id
        LEFT JOIN map_assets
            ON map_assets.id = map_scenes.asset_id
            AND map_assets.campaign_id = map_scenes.campaign_id
        WHERE games.id IS NULL
           OR games.campaign_id != map_scenes.campaign_id
           OR (map_scenes.asset_id IS NOT NULL AND map_assets.id IS NULL)
           OR (map_scenes.published = 1 AND map_scenes.asset_id IS NULL)
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise RuntimeError("Persisted map scene scope gecersiz.")
    asset_rows = db.execute(
        """
        SELECT map_assets.*, games.campaign_id AS uploader_campaign_id
        FROM map_assets
        LEFT JOIN members ON members.id = map_assets.uploader_id
        LEFT JOIN games ON games.id = members.game_id
        """
    ).fetchall()
    for row in asset_rows:
        content_type = row["content_type"]
        extension = (
            "png" if content_type == "image/png"
            else "jpg" if content_type == "image/jpeg"
            else None
        )
        byte_size = row["byte_size"]
        width = row["width"]
        height = row["height"]
        if (
            row["uploader_campaign_id"] != row["campaign_id"]
            or not isinstance(row["id"], str)
            or not 8 <= len(row["id"]) <= 64
            or not isinstance(row["original_name"], str)
            or not 1 <= len(row["original_name"].strip()) <= 160
            or not isinstance(row["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None
            or extension is None
            or row["storage_key"] != f"{row['sha256']}.{extension}"
            or not isinstance(byte_size, int)
            or not 1 <= byte_size <= 10 * 1024 * 1024
            or not isinstance(width, int)
            or not 64 <= width <= 8192
            or not isinstance(height, int)
            or not 64 <= height <= 8192
            or width * height > 64_000_000
            or not isinstance(row["created_at"], str)
            or not row["created_at"]
        ):
            raise RuntimeError("Persisted map asset metadata gecersiz.")
    scene_rows = db.execute("SELECT * FROM map_scenes").fetchall()
    for row in scene_rows:
        if (
            not isinstance(row["name"], str)
            or not 1 <= len(row["name"].strip()) <= 120
            or row["grid_type"] not in {"none", "square", "hex"}
            or not isinstance(row["grid_size_px"], int)
            or not 10 <= row["grid_size_px"] <= 512
            or not isinstance(row["distance_per_cell"], (int, float))
            or not 0.1 <= float(row["distance_per_cell"]) <= 1000
            or row["distance_unit"] not in {"ft", "m"}
            or not isinstance(row["viewport_x"], (int, float))
            or not -100_000 <= float(row["viewport_x"]) <= 100_000
            or not isinstance(row["viewport_y"], (int, float))
            or not -100_000 <= float(row["viewport_y"]) <= 100_000
            or not isinstance(row["viewport_zoom"], (int, float))
            or not 0.1 <= float(row["viewport_zoom"]) <= 8
            or row["published"] not in {0, 1}
            or not isinstance(row["revision"], int)
            or row["revision"] < 1
            or not isinstance(row["updated_at"], str)
            or not row["updated_at"]
        ):
            raise RuntimeError("Persisted map scene metadata gecersiz.")


def _migration_023_map_tokens(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS map_tokens (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            combatant_id TEXT NOT NULL,
            owner_member_id TEXT REFERENCES members(id) ON DELETE SET NULL,
            x REAL NOT NULL CHECK (x BETWEEN 0 AND 100000),
            y REAL NOT NULL CHECK (y BETWEEN 0 AND 100000),
            size_px INTEGER NOT NULL CHECK (size_px BETWEEN 16 AND 512),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (game_id, combatant_id)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_map_tokens_game
        ON map_tokens(game_id, updated_at)
        """
    )
    for operation in (
        "INSERT",
        "UPDATE OF game_id, campaign_id, owner_member_id",
    ):
        suffix = "insert" if operation == "INSERT" else "update"
        db.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_map_token_scope_{suffix}
            BEFORE {operation} ON map_tokens
            WHEN NOT EXISTS (
                SELECT 1 FROM games
                WHERE id = NEW.game_id AND campaign_id = NEW.campaign_id
            ) OR (
                NEW.owner_member_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM members
                    WHERE id = NEW.owner_member_id AND game_id = NEW.game_id
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'map token scope invalid');
            END
            """
        )
    rows = db.execute(
        """
        SELECT map_tokens.*, games.campaign_id AS game_campaign_id,
               members.game_id AS owner_game_id
        FROM map_tokens
        LEFT JOIN games ON games.id = map_tokens.game_id
        LEFT JOIN members ON members.id = map_tokens.owner_member_id
        """
    ).fetchall()
    duplicate = db.execute(
        """
        SELECT game_id, combatant_id
        FROM map_tokens
        GROUP BY game_id, combatant_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if duplicate is not None:
        raise RuntimeError("Persisted map token metadata gecersiz.")
    for row in rows:
        if (
            row["game_campaign_id"] != row["campaign_id"]
            or (
                row["owner_member_id"] is not None
                and row["owner_game_id"] != row["game_id"]
            )
            or not isinstance(row["id"], str)
            or not 8 <= len(row["id"]) <= 64
            or not isinstance(row["combatant_id"], str)
            or not 1 <= len(row["combatant_id"]) <= 64
            or not isinstance(row["x"], (int, float))
            or not 0 <= float(row["x"]) <= 100_000
            or not isinstance(row["y"], (int, float))
            or not 0 <= float(row["y"]) <= 100_000
            or not isinstance(row["size_px"], int)
            or not 16 <= row["size_px"] <= 512
            or not isinstance(row["revision"], int)
            or row["revision"] < 1
            or not isinstance(row["created_at"], str)
            or not row["created_at"]
            or not isinstance(row["updated_at"], str)
            or not row["updated_at"]
        ):
            raise RuntimeError("Persisted map token metadata gecersiz.")


def _migration_024_map_fog(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS map_fog_state (
            game_id TEXT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS map_fog_cells (
            game_id TEXT NOT NULL
                REFERENCES map_fog_state(game_id) ON DELETE CASCADE,
            cell_x INTEGER NOT NULL CHECK (cell_x BETWEEN 0 AND 8191),
            cell_y INTEGER NOT NULL CHECK (cell_y BETWEEN 0 AND 8191),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (game_id, cell_x, cell_y)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS map_transients (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            actor_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('ping', 'draw')),
            payload_json TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_map_transients_game_expiry
        ON map_transients(game_id, expires_at)
        """
    )
    for operation in ("INSERT", "UPDATE OF game_id, actor_id"):
        suffix = "insert" if operation == "INSERT" else "update"
        db.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_map_transient_scope_{suffix}
            BEFORE {operation} ON map_transients
            WHEN NOT EXISTS (
                SELECT 1 FROM members
                WHERE id = NEW.actor_id AND game_id = NEW.game_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'map transient scope invalid');
            END
            """
        )
    for operation in ("INSERT", "UPDATE OF game_id, campaign_id"):
        suffix = "insert" if operation == "INSERT" else "update"
        db.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_map_fog_scope_{suffix}
            BEFORE {operation} ON map_fog_state
            WHEN NOT EXISTS (
                SELECT 1 FROM games
                WHERE id = NEW.game_id AND campaign_id = NEW.campaign_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'map fog scope invalid');
            END
            """
        )
    db.execute(
        """
        INSERT OR IGNORE INTO map_fog_state (
            game_id, campaign_id, enabled, revision, updated_at
        )
        SELECT id, campaign_id, 0, 1,
               strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        FROM games
        """
    )
    invalid = db.execute(
        """
        SELECT map_fog_state.game_id
        FROM map_fog_state
        LEFT JOIN games ON games.id = map_fog_state.game_id
        WHERE games.id IS NULL
           OR games.campaign_id != map_fog_state.campaign_id
           OR map_fog_state.enabled NOT IN (0, 1)
           OR typeof(map_fog_state.revision) != 'integer'
           OR map_fog_state.revision < 1
           OR typeof(map_fog_state.updated_at) != 'text'
           OR length(map_fog_state.updated_at) < 1
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise RuntimeError("Persisted map fog metadata gecersiz.")
    invalid_cell = db.execute(
        """
        SELECT map_fog_cells.game_id
        FROM map_fog_cells
        LEFT JOIN map_fog_state
          ON map_fog_state.game_id = map_fog_cells.game_id
        WHERE map_fog_state.game_id IS NULL
           OR typeof(cell_x) != 'integer' OR cell_x NOT BETWEEN 0 AND 8191
           OR typeof(cell_y) != 'integer' OR cell_y NOT BETWEEN 0 AND 8191
           OR typeof(map_fog_cells.updated_at) != 'text'
           OR length(map_fog_cells.updated_at) < 1
        LIMIT 1
        """
    ).fetchone()
    if invalid_cell is not None:
        raise RuntimeError("Persisted map fog cell metadata gecersiz.")
    transient_rows = db.execute(
        """
        SELECT map_transients.*, members.game_id AS actor_game_id
        FROM map_transients
        LEFT JOIN members ON members.id = map_transients.actor_id
        """
    ).fetchall()
    for row in transient_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Persisted map transient metadata gecersiz."
            ) from error
        if not isinstance(payload, dict):
            raise RuntimeError("Persisted map transient metadata gecersiz.")
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
            created_at = datetime.fromisoformat(row["created_at"])
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "Persisted map transient metadata gecersiz."
            ) from error
        payload_valid = False
        if row["kind"] == "ping":
            payload_valid = (
                set(payload) == {"x", "y"}
                and all(
                    isinstance(payload[key], (int, float))
                    and not isinstance(payload[key], bool)
                    and 0 <= float(payload[key]) <= 100_000
                    for key in ("x", "y")
                )
            )
        elif row["kind"] == "draw":
            points = payload.get("points")
            payload_valid = (
                set(payload) == {"points"}
                and isinstance(points, list)
                and 2 <= len(points) <= 64
                and all(
                    isinstance(point, list)
                    and len(point) == 2
                    and all(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and 0 <= float(value) <= 100_000
                        for value in point
                    )
                    for point in points
                )
            )
        if (
            row["kind"] not in {"ping", "draw"}
            or row["actor_game_id"] != row["game_id"]
            or not isinstance(row["id"], str)
            or not 8 <= len(row["id"]) <= 64
            or not payload_valid
            or expires_at.tzinfo is None
            or created_at.tzinfo is None
            or expires_at <= created_at
        ):
            raise RuntimeError("Persisted map transient metadata gecersiz.")


def _migration_025_repair_vtt_backfill(db: sqlite3.Connection) -> None:
    """Repair databases that recorded an incomplete development VTT migration."""
    # The v22-v24 migrations are intentionally idempotent. Re-running them
    # recreates missing tables/triggers, backfills one scene and fog row for
    # every existing game, and still fails closed on corrupt persisted data.
    _migration_022_map_assets_and_scenes(db)
    _migration_023_map_tokens(db)
    _migration_024_map_fog(db)
    missing = db.execute(
        """
        SELECT games.id
        FROM games
        LEFT JOIN map_scenes ON map_scenes.game_id = games.id
        LEFT JOIN map_fog_state ON map_fog_state.game_id = games.id
        WHERE map_scenes.game_id IS NULL
           OR map_fog_state.game_id IS NULL
        LIMIT 1
        """
    ).fetchone()
    if missing is not None:
        raise RuntimeError("VTT backfill tamamlanamadi.")


def _migration_026_character_creation_gate(db: sqlite3.Connection) -> None:
    if "character_ready" not in _columns(db, "members"):
        db.execute(
            """
            ALTER TABLE members ADD COLUMN character_ready
            INTEGER NOT NULL DEFAULT 1 CHECK (character_ready IN (0, 1))
            """
        )

    from api.character_draft_engine import (
        DRAFT_SCHEMA_VERSION,
        CharacterDraftEngine,
        CharacterDraftValidationError,
    )
    from api.character_engine import CharacterEngine

    engine = CharacterDraftEngine(CharacterEngine())
    rows = db.execute(
        "SELECT * FROM character_drafts ORDER BY game_id, character_id"
    ).fetchall()
    migrated_rows: list[tuple[sqlite3.Row, dict]] = []
    for row in rows:
        try:
            data = json.loads(row["draft_json"])
            if data.get("schema_version") == 1:
                data = engine.migrate_v1(data)
            engine.validate_shape(data)
        except (
            AttributeError,
            json.JSONDecodeError,
            CharacterDraftValidationError,
        ) as error:
            raise RuntimeError(
                "Persisted character draft v2'ye yukseltilemedi."
            ) from error
        migrated_rows.append((row, data))

    db.execute("DROP TRIGGER IF EXISTS character_drafts_owner_game_insert")
    db.execute("DROP TRIGGER IF EXISTS character_drafts_owner_game_update")
    db.execute(
        f"""
        CREATE TABLE character_drafts_v2 (
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            character_id TEXT NOT NULL,
            owner_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            schema_version INTEGER NOT NULL
                CHECK (schema_version = {DRAFT_SCHEMA_VERSION}),
            draft_json TEXT NOT NULL,
            current_step TEXT NOT NULL CHECK (
                current_step IN (
                    'basics', 'abilities', 'class', 'species', 'background',
                    'proficiencies', 'equipment', 'spells', 'review'
                )
            ),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'published')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT,
            PRIMARY KEY (game_id, character_id)
        )
        """
    )
    for row, data in migrated_rows:
        db.execute(
            """
            INSERT INTO character_drafts_v2 (
                game_id, character_id, owner_id, schema_version, draft_json,
                current_step, revision, status, created_at, updated_at,
                published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["game_id"],
                row["character_id"],
                row["owner_id"],
                DRAFT_SCHEMA_VERSION,
                json.dumps(data, ensure_ascii=False),
                row["current_step"],
                row["revision"],
                row["status"],
                row["created_at"],
                row["updated_at"],
                row["published_at"],
            ),
        )
    db.execute("DROP TABLE character_drafts")
    db.execute("ALTER TABLE character_drafts_v2 RENAME TO character_drafts")
    db.execute(
        """
        CREATE INDEX idx_character_drafts_owner
        ON character_drafts (game_id, owner_id, status)
        """
    )
    for operation in ("INSERT", "UPDATE OF game_id, character_id, owner_id"):
        suffix = "insert" if operation == "INSERT" else "update"
        db.execute(
            f"""
            CREATE TRIGGER character_drafts_owner_game_{suffix}
            BEFORE {operation} ON character_drafts
            WHEN NOT EXISTS (
                SELECT 1 FROM members
                WHERE id = NEW.owner_id
                  AND game_id = NEW.game_id
                  AND character_id = NEW.character_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'draft owner/game/character mismatch');
            END
            """
        )

    db.execute(
        """
        UPDATE members
        SET character_ready = CASE
            WHEN role != 'player' THEN 1
            WHEN EXISTS (
                SELECT 1 FROM character_drafts AS drafts
                WHERE drafts.game_id = members.game_id
                  AND drafts.character_id = members.character_id
                  AND drafts.owner_id = members.id
                  AND drafts.status = 'published'
            ) THEN 1
            ELSE 0
        END
        """
    )
    invalid_member = db.execute(
        """
        SELECT 1 FROM members
        WHERE character_ready NOT IN (0, 1)
           OR (role = 'player' AND character_id IS NULL)
        LIMIT 1
        """
    ).fetchone()
    if invalid_member is not None:
        raise RuntimeError("Character creation member metadata gecersiz.")


MIGRATIONS: tuple[Migration, ...] = (
    (1, "initial_multiplayer_schema", _migration_001_initial_multiplayer_schema),
    (2, "dm_handover", _migration_002_dm_handover),
    (3, "campaign_sessions", _migration_003_campaign_sessions),
    (4, "revision_idempotency", _migration_004_revision_idempotency),
    (5, "public_auth", _migration_005_public_auth),
    (6, "bind_websocket_tickets", _migration_006_bind_websocket_tickets),
    (7, "auth_configuration", _migration_007_auth_configuration),
    (8, "srd_521_ruleset", _migration_008_srd_521_ruleset),
    (9, "character_aggregate", _migration_009_character_aggregate),
    (10, "character_resources", _migration_010_character_resources),
    (11, "resource_turn_serial", _migration_011_resource_turn_serial),
    (12, "turn_action_ledger", _migration_012_turn_action_ledger),
    (13, "character_inventory", _migration_013_character_inventory),
    (14, "character_actions", _migration_014_character_actions),
    (15, "character_drafts", _migration_015_character_drafts),
    (16, "session_zero", _migration_016_session_zero),
    (17, "session_workspace", _migration_017_session_workspace),
    (18, "encounter_library", _migration_018_encounter_library),
    (19, "advanced_live_encounter", _migration_019_advanced_live_encounter),
    (20, "typed_roll_events", _migration_020_typed_roll_events),
    (21, "dice_preferences", _migration_021_dice_preferences),
    (22, "map_assets_and_scenes", _migration_022_map_assets_and_scenes),
    (23, "map_tokens", _migration_023_map_tokens),
    (24, "map_fog", _migration_024_map_fog),
    (25, "repair_vtt_backfill", _migration_025_repair_vtt_backfill),
    (26, "character_creation_gate", _migration_026_character_creation_gate),
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1][0]


def apply_migrations(
    db: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> None:
    """Apply ordered migrations on a connection owned by the caller's transaction."""
    versions = [version for version, _, _ in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError("Migration version'lari 1'den baslayan kesintisiz bir sira olmali.")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        row["version"]: row["name"]
        for row in db.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        )
    }
    latest_known = versions[-1] if versions else 0
    unknown = sorted(version for version in applied if version > latest_known)
    if unknown:
        raise RuntimeError(
            f"Veritabani bu uygulamadan daha yeni bir schema kullaniyor: {unknown[-1]}."
        )

    for version, name, action in migrations:
        recorded_name = applied.get(version)
        if recorded_name is not None:
            if recorded_name != name:
                raise RuntimeError(
                    f"Migration {version} adi uyusmuyor: {recorded_name!r} != {name!r}."
                )
            continue
        action(db)
        db.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, datetime.now(UTC).isoformat()),
        )
