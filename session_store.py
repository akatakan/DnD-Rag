import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from game_state import GameState


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
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

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool')),
                    content TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    activity_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id);
                """
            )

    def create_session(self, title: str = "Yeni Oturum") -> str:
        session_id = uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (session_id, title.strip() or "Yeni Oturum", json.dumps(GameState().to_dict()), now, now),
            )
        return session_id

    def list_sessions(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def rename_session(self, session_id: str, title: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip() or "Yeni Oturum", datetime.now(UTC).isoformat(), session_id),
            )

    def load_state(self, session_id: str) -> GameState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Oturum bulunamadı: {session_id}")
        return GameState.from_dict(json.loads(row["state_json"]))

    def save_state(self, session_id: str, state: GameState) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET state_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(state.to_dict(), ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    session_id,
                ),
            )

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: list | None = None,
        activity: list | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO messages
                (session_id, role, content, sources_json, activity_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(sources or [], ensure_ascii=False),
                    json.dumps(activity or [], ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )

    def messages(self, session_id: str, limit: int | None = None) -> list[dict]:
        query = """SELECT role, content, sources_json, activity_json, created_at
                   FROM messages WHERE session_id = ? ORDER BY id"""
        params: tuple = (session_id,)
        if limit is not None:
            query = """SELECT * FROM (
                       SELECT role, content, sources_json, activity_json, created_at, id
                       FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
                       ) ORDER BY id"""
            params = (session_id, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "sources": json.loads(row["sources_json"]),
                "activity": json.loads(row["activity_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def clear_messages(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    def memory_context(self, session_id: str, limit: int = 8) -> str:
        messages = self.messages(session_id, limit=limit)
        if not messages:
            return "Önceki konuşma yok."
        labels = {"user": "Oyuncu", "assistant": "Asistan", "tool": "Araç"}
        return "\n".join(
            f"{labels.get(message['role'], message['role'])}: {message['content'][:1000]}"
            for message in messages
        )
