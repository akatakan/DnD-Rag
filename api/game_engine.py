from copy import deepcopy
from uuid import uuid4

from api.models import AuthContext, CommandRequest
from api.store import GameStore
from dice import DiceError, roll


class CommandError(ValueError):
    pass


class GameEngine:
    def __init__(self, store: GameStore):
        self.store = store

    def require_active_dm(self, auth: AuthContext) -> dict:
        game = self.store.game(auth.game_id)
        if auth.role not in {"dm", "co_dm"} or game["active_dm_id"] != auth.member_id:
            raise CommandError("Bu islem yalnizca aktif DM tarafindan yapilabilir.")
        return game

    @staticmethod
    def require_owner(auth: AuthContext) -> None:
        if not auth.is_owner:
            raise CommandError("Bu islem yalnizca oyun sahibi tarafindan yapilabilir.")

    @staticmethod
    def character_for(state: dict, character_id: str) -> dict:
        character = state["characters"].get(character_id)
        if character is None:
            raise CommandError("Karakter bulunamadi.")
        return character

    def apply(self, auth: AuthContext, command: CommandRequest) -> dict:
        game = self.store.game(auth.game_id)
        state = deepcopy(game["state"])
        command_type, payload = command.type, command.payload

        if command_type == "roll":
            expression = str(payload.get("expression", "1d20"))
            try:
                result = roll(expression)
            except DiceError as error:
                raise CommandError(str(error)) from error
            visibility = str(payload.get("visibility", "public"))
            if visibility == "dm_only" and auth.role == "player":
                visibility = f"player:{auth.member_id}"
            event = self.store.add_event(
                auth.game_id, "dice_rolled", auth.member_id, visibility,
                {"expression": result.expression, "rolls": result.rolls, "kept": result.kept,
                 "modifier": result.modifier, "total": result.total},
            )
            return {"event": event, "state": state}

        if command_type in {"request_damage", "request_heal"}:
            if auth.role != "player" or not auth.character_id:
                raise CommandError("Yalnizca oyuncular kendi karakterleri icin talep olusturabilir.")
            amount = max(0, int(payload.get("amount", 0)))
            request = self.store.create_request(
                auth, command_type.removeprefix("request_"),
                {"character_id": auth.character_id, "amount": amount},
            )
            event = self.store.add_event(auth.game_id, "change_requested", auth.member_id, "dm_only", request)
            return {"event": event, "request": request, "state": state}

        if command_type in {"approve_request", "reject_request"}:
            self.require_active_dm(auth)
            request = self.store.resolve_request(
                auth.game_id, str(payload["request_id"]),
                "approved" if command_type == "approve_request" else "rejected",
            )
            if command_type == "approve_request":
                nested = CommandRequest(
                    type="apply_damage" if request["type"] == "damage" else "apply_heal",
                    payload=request["payload"],
                )
                return self.apply(auth, nested)
            event = self.store.add_event(auth.game_id, "change_rejected", auth.member_id, "party", request)
            return {"event": event, "state": state}

        if command_type in {"apply_damage", "apply_heal"}:
            self.require_active_dm(auth)
            character = self.character_for(state, str(payload["character_id"]))
            amount = max(0, int(payload.get("amount", 0)))
            if command_type == "apply_damage":
                absorbed = min(character.get("temp_hp", 0), amount)
                character["temp_hp"] -= absorbed
                effective = amount - absorbed
                character["hp"] = max(0, character["hp"] - effective)
                event_type = "character_damaged"
            else:
                before = character["hp"]
                character["hp"] = min(character["max_hp"], character["hp"] + amount)
                effective = character["hp"] - before
                event_type = "character_healed"
            event_payload = {"character_id": character["id"], "amount": amount, "effective": effective,
                             "hp": character["hp"], "temp_hp": character["temp_hp"]}

        elif command_type == "update_character":
            if auth.role != "player":
                self.require_active_dm(auth)
            character_id = auth.character_id if auth.role == "player" else str(payload["character_id"])
            if not character_id:
                raise CommandError("Karakter bulunamadi.")
            character = self.character_for(state, character_id)
            for key in {"name", "class_name", "level", "ac", "max_hp"} & payload.keys():
                character[key] = payload[key]
            character["level"] = min(20, max(1, int(character["level"])))
            character["ac"] = min(40, max(0, int(character["ac"])))
            character["max_hp"] = max(1, int(character["max_hp"]))
            character["hp"] = min(character["hp"], character["max_hp"])
            event_type, event_payload = "character_updated", {"character_id": character_id}

        elif command_type in {"add_item", "remove_item"}:
            if auth.role != "player":
                self.require_active_dm(auth)
            character_id = auth.character_id if auth.role == "player" else str(payload["character_id"])
            character = self.character_for(state, character_id)
            item = str(payload.get("item", "")).strip()
            if not item:
                raise CommandError("Esya adi bos olamaz.")
            if command_type == "add_item":
                character["inventory"].append(item)
                event_type = "item_added"
            else:
                if item not in character["inventory"]:
                    raise CommandError("Esya envanterde bulunamadi.")
                character["inventory"].remove(item)
                event_type = "item_removed"
            event_payload = {"character_id": character_id, "item": item}

        elif command_type == "set_dm_mode":
            self.require_active_dm(auth)
            mode = str(payload.get("mode"))
            if mode not in {"human", "assisted", "ai"}:
                raise CommandError("Gecersiz DM modu.")
            self.store.set_dm_mode(auth.game_id, mode)
            event_type, event_payload = "dm_mode_changed", {"mode": mode}

        elif command_type == "assign_co_dm":
            self.require_owner(auth)
            member_id = str(payload.get("member_id", ""))
            current_co_dm = next(
                (item for item in self.store.members(auth.game_id) if item["role"] == "co_dm"),
                None,
            )
            if current_co_dm and game["active_dm_id"] == current_co_dm["id"] and member_id != current_co_dm["id"]:
                raise CommandError("Aktif co-DM degistirilmeden once DM kontrolunu geri alin.")
            member = self.store.member(auth.game_id, member_id)
            if member is None or member["role"] != "player":
                raise CommandError("Co-DM olarak atanabilecek oyuncu bulunamadi.")
            self.store.assign_co_dm(auth.game_id, member_id)
            event_type, event_payload = "co_dm_assigned", {"member_id": member_id, "name": member["name"]}

        elif command_type == "remove_co_dm":
            self.require_owner(auth)
            co_dm = next((item for item in self.store.members(auth.game_id) if item["role"] == "co_dm"), None)
            if co_dm and game["active_dm_id"] == co_dm["id"]:
                raise CommandError("Aktif co-DM kaldirilmadan once DM kontrolunu geri alin.")
            self.store.assign_co_dm(auth.game_id, None)
            event_type, event_payload = "co_dm_removed", {"member_id": co_dm["id"] if co_dm else None}

        elif command_type == "set_fallback_mode":
            if not auth.is_owner and game["active_dm_id"] != auth.member_id:
                raise CommandError("Yedek DM politikasini yalnizca oyun sahibi veya aktif DM degistirebilir.")
            mode = str(payload.get("mode", ""))
            try:
                self.store.set_fallback_mode(auth.game_id, mode)
            except ValueError as error:
                raise CommandError(str(error)) from error
            event_type, event_payload = "fallback_dm_mode_changed", {"mode": mode}

        elif command_type == "accept_dm_handover":
            handover = game["handover"]
            if auth.role != "co_dm" or handover.get("status") != "offered" or handover.get("candidate_id") != auth.member_id:
                raise CommandError("Kabul edilebilecek bir DM devir teklifi yok.")
            previous_dm_id = game["active_dm_id"]
            self.store.activate_dm(auth.game_id, auth.member_id, "human")
            event_type, event_payload = "dm_control_transferred", {
                "from_member_id": previous_dm_id, "to_member_id": auth.member_id,
            }

        elif command_type == "vote_ai_takeover":
            handover = game["handover"]
            eligible = handover.get("eligible_voters", [])
            if auth.role != "player" or handover.get("status") != "vote_ai" or auth.member_id not in eligible:
                raise CommandError("Aktif bir AI DM oylamasina katilamazsiniz.")
            votes = list(dict.fromkeys([*handover.get("votes", []), auth.member_id]))
            required = int(handover.get("required", 1))
            if len(votes) >= required:
                self.store.set_dm_mode(auth.game_id, "ai")
                self.store.cancel_handover(auth.game_id)
                event_type, event_payload = "ai_dm_takeover_approved", {"votes": len(votes), "required": required}
            else:
                handover["votes"] = votes
                self.store.set_handover(auth.game_id, handover)
                event_type, event_payload = "ai_takeover_vote_cast", {
                    "member_id": auth.member_id, "votes": len(votes), "required": required,
                }

        elif command_type == "reclaim_dm_control":
            self.require_owner(auth)
            previous_dm_id = game["active_dm_id"]
            self.store.activate_dm(auth.game_id, auth.member_id, "human")
            event_type, event_payload = "dm_control_reclaimed", {
                "from_member_id": previous_dm_id, "to_member_id": auth.member_id,
            }

        elif command_type == "update_scene":
            self.require_active_dm(auth)
            for key in ("title", "description", "public_notes"):
                if key in payload:
                    state["scene"][key] = str(payload[key])[:4000]
            event_type, event_payload = "scene_updated", dict(state["scene"])

        elif command_type == "add_combatant":
            self.require_active_dm(auth)
            state["combatants"].append({
                "id": str(payload.get("id") or payload.get("character_id") or uuid4().hex),
                "name": str(payload["name"]), "initiative": int(payload.get("initiative", 0)),
                "hp": payload.get("hp"), "kind": str(payload.get("kind", "monster")),
                "hidden": bool(payload.get("hidden", False)),
            })
            event_type, event_payload = "combatant_added", {"name": payload["name"]}

        elif command_type == "start_encounter":
            self.require_active_dm(auth)
            if not state["combatants"]:
                raise CommandError("Encounter icin katilimci ekleyin.")
            state["combatants"].sort(key=lambda item: item["initiative"], reverse=True)
            state.update(encounter_status="active", round=1, turn_index=0)
            event_type, event_payload = "encounter_started", {"round": 1}

        elif command_type == "next_turn":
            self.require_active_dm(auth)
            if state["encounter_status"] != "active":
                raise CommandError("Aktif encounter yok.")
            state["turn_index"] += 1
            if state["turn_index"] >= len(state["combatants"]):
                state["turn_index"] = 0
                state["round"] += 1
            event_type, event_payload = "turn_advanced", {
                "round": state["round"], "turn_index": state["turn_index"],
            }

        elif command_type == "complete_encounter":
            self.require_active_dm(auth)
            state["encounter_status"] = "completed"
            event_type, event_payload = "encounter_completed", {"round": state["round"]}

        else:
            raise CommandError("Desteklenmeyen komut.")

        self.store.save_state(auth.game_id, state)
        event = self.store.add_event(auth.game_id, event_type, auth.member_id, "party", event_payload)
        return {"event": event, "state": state}


