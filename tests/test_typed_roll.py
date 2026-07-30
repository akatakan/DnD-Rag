import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from api.game_engine import CommandError, GameEngine
from api.models import CommandRequest
from api.store import GameStore


class TypedRollTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "game.db"
        self.store = GameStore(self.path)
        created = self.store.create_game("Typed Dice", "DM", "human")
        joined = self.store.join_game(created["invite_code"], "Riva")
        second = self.store.join_game(created["invite_code"], "Gareth")
        self.player_token = joined["token"]
        self.player = self.store.authenticate(joined["token"])
        self.second = self.store.authenticate(second["token"])
        self.dm = self.store.authenticate(created["token"])
        self.engine = GameEngine(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def command(
        self,
        action_id: str,
        *,
        visibility: str = "party",
        actor_character_id: str | None = None,
        mode: str = "normal",
        count: int = 2,
        sides: int = 6,
        modifier: int = 3,
    ) -> CommandRequest:
        return CommandRequest(
            type="roll_intent",
            payload={
                "actor_character_id": (
                    self.player.character_id
                    if actor_character_id is None
                    else actor_character_id
                ),
                "action": "custom_roll",
                "visibility": visibility,
                "context": "global_fab",
                "dice": {
                    "count": count,
                    "sides": sides,
                    "modifier": modifier,
                    "mode": mode,
                },
            },
            client_action_id=action_id,
        )

    @patch("api.game_engine.roll")
    def test_server_builds_typed_intent_and_persists_event_metadata(self, mocked):
        mocked.return_value.expression = "2d6+3"
        mocked.return_value.rolls = (4, 5)
        mocked.return_value.kept = (4, 5)
        mocked.return_value.modifier = 3
        mocked.return_value.total = 12

        response = self.engine.apply(
            self.player, self.command("typed-roll-main-001")
        )

        mocked.assert_called_once_with("2d6+3")
        event = response["event"]
        self.assertEqual(event["type"], "typed_roll_resolved")
        self.assertEqual(event["visibility"], "party")
        intent = event["payload"]["intent"]
        self.assertEqual(intent["schema_version"], 1)
        self.assertEqual(intent["actor"]["member_id"], self.player.member_id)
        self.assertEqual(
            intent["actor"]["character_id"], self.player.character_id
        )
        self.assertEqual(intent["action"]["kind"], "custom_roll")
        self.assertEqual(intent["context"]["surface"], "global_fab")
        self.assertEqual(intent["roll"]["expression"], "2d6+3")
        with self.store.connect() as db:
            row = db.execute(
                """
                SELECT typed_intent_id, intent_schema_version
                FROM events WHERE id = ?
                """,
                (event["id"],),
            ).fetchone()
        self.assertEqual(tuple(row), (intent["intent_id"], 1))

    def test_advantage_contract_is_strict_and_expression_is_server_owned(self):
        valid = self.command(
            "typed-roll-advantage-001",
            mode="advantage",
            count=2,
            sides=20,
            modifier=-1,
        )
        with patch("api.game_engine.roll") as mocked:
            mocked.return_value.expression = "2d20kh1-1"
            mocked.return_value.rolls = (3, 17)
            mocked.return_value.kept = (17,)
            mocked.return_value.modifier = -1
            mocked.return_value.total = 16
            response = self.engine.apply(self.player, valid)
        mocked.assert_called_once_with("2d20kh1-1")
        self.assertEqual(
            response["event"]["payload"]["intent"]["roll"]["mode"],
            "advantage",
        )

        with self.assertRaises(ValidationError):
            self.command(
                "typed-roll-invalid-001",
                mode="advantage",
                count=3,
                sides=20,
            )
        payload = dict(valid.payload)
        payload["expression"] = "100d100+99999"
        with self.assertRaises(ValidationError):
            CommandRequest(type="roll_intent", payload=payload)

    def test_player_cannot_spoof_actor_and_private_roll_is_redacted(self):
        with self.assertRaisesRegex(CommandError, "kendi karakterini"):
            self.engine.apply(
                self.player,
                self.command(
                    "typed-roll-spoof-001",
                    actor_character_id=self.second.character_id,
                ),
            )

        response = self.engine.apply(
            self.player,
            self.command("typed-roll-private-001", visibility="private"),
        )
        self.assertEqual(
            response["event"]["visibility"], f"player:{self.player.member_id}"
        )
        own_ids = {event["id"] for event in self.store.events(self.player)}
        other_ids = {event["id"] for event in self.store.events(self.second)}
        dm_ids = {event["id"] for event in self.store.events(self.dm)}
        self.assertIn(response["event"]["id"], own_ids)
        self.assertNotIn(response["event"]["id"], other_ids)
        self.assertIn(response["event"]["id"], dm_ids)

    def test_roll_uses_current_context_without_leaking_dm_encounter_metadata(self):
        game = self.store.game(self.player.game_id)
        state = game["state"]
        state.update(
            encounter_status="active",
            active_encounter_id="internal-encounter-draft",
            active_encounter_revision=7,
            round=3,
            turn_index=0,
            combatants=[
                {
                    "id": "hidden-current-actor",
                    "name": "Hidden",
                    "kind": "monster",
                    "initiative": 20,
                    "tie_breaker": 0,
                    "hp": 10,
                    "max_hp": 10,
                    "hidden": True,
                }
            ],
        )
        self.store.save_state(self.player.game_id, state)
        stale = self.command("typed-roll-stale-context-001")
        stale.expected_revision = 0

        response = self.engine.apply(self.player, stale)

        context = response["event"]["payload"]["intent"]["context"]
        self.assertEqual(context["round"], 3)
        self.assertIsNone(context["encounter_id"])
        self.assertIsNone(context["turn_index"])
        self.assertEqual(
            response["revision"],
            self.store.game(self.player.game_id)["state_revision"],
        )

    def test_inactive_co_dm_can_roll_as_own_character_but_not_spoof(self):
        self.store.assign_co_dm(self.player.game_id, self.player.member_id)
        co_dm = self.store.authenticate(self.player_token)
        self.assertNotEqual(
            self.store.game(co_dm.game_id)["active_dm_id"], co_dm.member_id
        )

        own = self.engine.apply(
            co_dm, self.command("typed-roll-codm-own-001")
        )
        self.assertEqual(
            own["event"]["payload"]["intent"]["actor"]["character_id"],
            co_dm.character_id,
        )
        with self.assertRaisesRegex(CommandError, "aktif DM"):
            self.engine.apply(
                co_dm,
                self.command(
                    "typed-roll-codm-spoof-001",
                    actor_character_id=self.second.character_id,
                ),
            )

    def test_duplicate_and_concurrent_retry_create_one_game_log_event(self):
        command = self.command("typed-roll-race-001")
        barrier = threading.Barrier(2)
        responses: list[dict] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def run():
            try:
                store = GameStore(self.path)
                engine = GameEngine(store)
                auth = store.authenticate(self.player_token)
                barrier.wait()
                result = engine.apply(auth, command)
                with lock:
                    responses.append(result)
            except Exception as error:  # pragma: no cover - assertion reports it
                with lock:
                    errors.append(error)

        workers = [threading.Thread(target=run) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)

        self.assertFalse(errors)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(
            sorted(item["replayed"] for item in responses), [False, True]
        )
        typed_events = [
            event
            for event in self.store.events(self.player)
            if event["type"] == "typed_roll_resolved"
        ]
        self.assertEqual(len(typed_events), 1)
        self.assertEqual(responses[0]["event"]["id"], responses[1]["event"]["id"])

    def test_migration_rejects_invalid_typed_event_metadata(self):
        game_id = self.player.game_id
        with self.assertRaisesRegex(ValueError, "metadata gecersiz"):
            self.store.add_event(
                game_id,
                "typed_roll_resolved",
                self.player.member_id,
                "party",
                {
                    "intent": {
                        "intent_id": "typed-float-schema-001",
                        "schema_version": 1.0,
                    }
                },
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute(
                    """
                    INSERT INTO events (
                        game_id, type, actor_id, visibility, payload_json,
                        created_at, typed_intent_id, intent_schema_version
                    ) VALUES (?, 'typed_roll_resolved', ?, 'party', '{}',
                        '2026-01-01T00:00:00Z', 'short', 1)
                    """,
                    (game_id, self.player.member_id),
                )
        event = self.engine.apply(
            self.player, self.command("typed-roll-trigger-001")
        )["event"]
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute(
                    """
                    UPDATE events SET intent_schema_version = 2 WHERE id = ?
                    """,
                    (event["id"],),
                )

        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute(
                    """
                    INSERT INTO events (
                        game_id, type, actor_id, visibility, payload_json,
                        created_at, typed_intent_id, intent_schema_version
                    ) VALUES (?, 'typed_roll_resolved', ?, 'party', '{}',
                        '2026-01-01T00:00:00Z', NULL, NULL)
                    """,
                    (game_id, self.player.member_id),
                )

        other = self.store.create_game("Other", "Other DM", "human")
        intent_id = "typed-cross-game-actor-001"
        payload = json.dumps(
            {"intent": {"intent_id": intent_id, "schema_version": 1}}
        )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute(
                    """
                    INSERT INTO events (
                        game_id, type, actor_id, visibility, payload_json,
                        created_at, typed_intent_id, intent_schema_version
                    ) VALUES (?, 'typed_roll_resolved', ?, 'party', ?,
                        '2026-01-01T00:00:00Z', ?, 1)
                    """,
                    (
                        game_id,
                        other["member_id"],
                        payload,
                        intent_id,
                    ),
                )

    def test_v20_reapply_backfills_existing_typed_metadata(self):
        event = self.engine.apply(
            self.player, self.command("typed-roll-backfill-001")
        )["event"]
        with self.store.connect() as db:
            db.execute("DROP TRIGGER trg_events_typed_intent_insert")
            db.execute("DROP TRIGGER trg_events_typed_intent_update")
            db.execute(
                """
                UPDATE events
                SET typed_intent_id = NULL, intent_schema_version = NULL
                WHERE id = ?
                """,
                (event["id"],),
            )
            db.execute("DELETE FROM schema_migrations WHERE version = 20")

        GameStore(self.path)

        with self.store.connect() as db:
            row = db.execute(
                """
                SELECT typed_intent_id, intent_schema_version
                FROM events WHERE id = ?
                """,
                (event["id"],),
            ).fetchone()
        self.assertEqual(
            tuple(row),
            (
                event["payload"]["intent"]["intent_id"],
                1,
            ),
        )


if __name__ == "__main__":
    unittest.main()
