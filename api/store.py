import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from api.models import AuthContext, DMMode


def now() -> str:
    return datetime.now(UTC).isoformat()


class GameStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _columns(db, table: str) -> set[str]:
        return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS games (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, invite_code TEXT UNIQUE NOT NULL,
                    dm_mode TEXT NOT NULL, state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    owner_id TEXT, active_dm_id TEXT, fallback_dm_mode TEXT NOT NULL DEFAULT 'assisted',
                    handover_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS members (
                    id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, role TEXT NOT NULL, character_id TEXT, token TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    type TEXT NOT NULL, actor_id TEXT NOT NULL, visibility TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    actor_id TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_game ON events(game_id, id);
                CREATE INDEX IF NOT EXISTS idx_members_token ON members(token);
                """
            )
            game_columns = self._columns(db, "games")
            migrations = {
                "owner_id": "ALTER TABLE games ADD COLUMN owner_id TEXT",
                "active_dm_id": "ALTER TABLE games ADD COLUMN active_dm_id TEXT",
                "fallback_dm_mode": "ALTER TABLE games ADD COLUMN fallback_dm_mode TEXT NOT NULL DEFAULT 'assisted'",
                "handover_json": "ALTER TABLE games ADD COLUMN handover_json TEXT NOT NULL DEFAULT '{}'",
            }
            for column, statement in migrations.items():
                if column not in game_columns:
                    db.execute(statement)
            db.execute(
                """UPDATE games SET
                owner_id = COALESCE(owner_id, (SELECT id FROM members WHERE members.game_id = games.id AND role = 'dm' ORDER BY created_at LIMIT 1)),
                active_dm_id = COALESCE(active_dm_id, owner_id,
                    (SELECT id FROM members WHERE members.game_id = games.id AND role = 'dm' ORDER BY created_at LIMIT 1))"""
            )

    @staticmethod
    def _initial_state() -> dict:
        return {
            "round": 0, "turn_index": 0, "encounter_status": "idle",
            "combatants": [], "characters": {},
            "scene": {"title": "New Adventure", "description": "", "public_notes": ""},
        }

    def create_game(self, name: str, dm_name: str, dm_mode: DMMode) -> dict:
        game_id, member_id = uuid4().hex, uuid4().hex
        invite_code, token, timestamp = secrets.token_hex(4).upper(), secrets.token_urlsafe(32), now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO games
                (id, name, invite_code, dm_mode, state_json, created_at, updated_at,
                 owner_id, active_dm_id, fallback_dm_mode, handover_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'assisted', '{}')""",
                (game_id, name, invite_code, dm_mode, json.dumps(self._initial_state()), timestamp, timestamp, member_id, member_id),
            )
            db.execute(
                """INSERT INTO members
                (id, game_id, name, role, character_id, token, created_at)
                VALUES (?, ?, ?, 'dm', NULL, ?, ?)""",
                (member_id, game_id, dm_name, token, timestamp),
            )
        self.add_event(game_id, "game_created", member_id, "public", {"name": name, "dm_mode": dm_mode})
        return {"game_id": game_id, "member_id": member_id, "invite_code": invite_code, "token": token, "role": "dm"}

    def join_game(self, invite_code: str, player_name: str) -> dict:
        member_id, character_id, token, timestamp = uuid4().hex, uuid4().hex, secrets.token_urlsafe(32), now()
        with self.connect() as db:
            game = db.execute("SELECT id, state_json FROM games WHERE invite_code = ?", (invite_code.upper(),)).fetchone()
            if game is None:
                raise KeyError("Davet kodu bulunamadı.")
            state = json.loads(game["state_json"])
            state["characters"][character_id] = {
                "id": character_id, "owner_id": member_id, "name": player_name,
                "class_name": "Fighter", "level": 1, "ac": 10, "max_hp": 10,
                "hp": 10, "temp_hp": 0, "conditions": [], "inventory": [],
            }
            db.execute(
                """INSERT INTO members
                (id, game_id, name, role, character_id, token, created_at)
                VALUES (?, ?, ?, 'player', ?, ?, ?)""",
                (member_id, game["id"], player_name, character_id, token, timestamp),
            )
            db.execute("UPDATE games SET state_json = ?, updated_at = ? WHERE id = ?", (json.dumps(state), timestamp, game["id"]))
        self.add_event(game["id"], "player_joined", member_id, "public", {"name": player_name, "character_id": character_id})
        return {"game_id": game["id"], "member_id": member_id, "character_id": character_id, "token": token, "role": "player"}

    def authenticate(self, token: str) -> AuthContext | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT members.game_id, members.id, members.role, members.character_id,
                games.owner_id FROM members JOIN games ON games.id = members.game_id
                WHERE members.token = ?""", (token,),
            ).fetchone()
        if row is None:
            return None
        return AuthContext(
            game_id=row["game_id"], member_id=row["id"], role=row["role"],
            character_id=row["character_id"], is_owner=row["owner_id"] == row["id"],
        )

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

    def activate_dm(self, game_id: str, member_id: str, mode: str = "human") -> None:
        member = self.member(game_id, member_id)
        if member["role"] not in {"dm", "co_dm"}:
            raise ValueError("Yalnızca DM veya co-DM kontrolü devralabilir.")
        with self.connect() as db:
            db.execute("UPDATE games SET active_dm_id = ?, dm_mode = ?, handover_json = '{}', updated_at = ? WHERE id = ?", (member_id, mode, now(), game_id))

    def set_dm_mode(self, game_id: str, mode: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE games SET dm_mode = ?, updated_at = ? WHERE id = ?", (mode, now(), game_id))

    def add_event(self, game_id: str, event_type: str, actor_id: str, visibility: str, payload: dict) -> dict:
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute("INSERT INTO events (game_id, type, actor_id, visibility, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)", (game_id, event_type, actor_id, visibility, json.dumps(payload, ensure_ascii=False), timestamp))
        return {"id": cursor.lastrowid, "game_id": game_id, "type": event_type, "actor_id": actor_id, "visibility": visibility, "payload": payload, "created_at": timestamp}

    @staticmethod
    def can_view(visibility: str, auth: AuthContext) -> bool:
        return visibility in {"public", "party"} or auth.role in {"dm", "co_dm"} or visibility == f"player:{auth.member_id}"

    def events(self, auth: AuthContext, after: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE game_id = ? AND id > ? ORDER BY id", (auth.game_id, after)).fetchall()
        return [{"id": row["id"], "type": row["type"], "actor_id": row["actor_id"], "visibility": row["visibility"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]} for row in rows if self.can_view(row["visibility"], auth)]

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
