from copy import deepcopy
import hashlib
import hmac
import json
from uuid import uuid4

from api.character_engine import CharacterEngine, CharacterValidationError
from api.action_engine import ActionEngine, ActionValidationError
from api.character_draft_engine import CharacterDraftValidationError
from api.encounter_engine import EncounterDraftConflict, EncounterValidationError
from api.inventory_engine import InventoryEngine, InventoryValidationError
from api.models import AuthContext, CommandRequest
from api.resource_engine import ResourceEngine, ResourceValidationError
from api.store import GameStore
from dice import DiceError, roll


class CommandError(ValueError):
    pass


class RevisionConflict(CommandError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Oyun durumu değişti: beklenen revision {expected}, güncel revision {actual}."
        )


NON_STATE_UNDO_COMMANDS = {
    "roll", "roll_intent", "approve_request", "publish_character_draft",
}


class GameEngine:
    def __init__(
        self, store: GameStore, character_engine: CharacterEngine | None = None
    ):
        self.store = store
        self.character_engine = character_engine or store.character_engine
        self.resource_engine = ResourceEngine(self.character_engine.catalog)
        self.inventory_engine = InventoryEngine(self.character_engine.catalog)
        self.action_engine = ActionEngine(self.character_engine.catalog)

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
        # BEGIN IMMEDIATE prevents two API workers from reading the same JSON
        # snapshot and silently overwriting one another. Nested applies (request
        # approval) reuse this transaction, so request, state and event commit or
        # roll back together.
        with self.store.transaction():
            request_hash = self._request_hash(command)
            if command.client_action_id:
                receipt = self.store.command_receipt(
                    auth.game_id, auth.member_id, command.client_action_id
                )
                if receipt is not None:
                    if not hmac.compare_digest(
                        receipt["request_hash"], request_hash
                    ):
                        raise CommandError(
                            "client_action_id farklı bir komut için yeniden kullanılamaz."
                        )
                    return {
                        **receipt["response"],
                        "replayed": True,
                    }

            game = self.store.game(auth.game_id)
            revision = int(game["state_revision"])
            before_state = deepcopy(game["state"])
            if (
                command.type not in {"roll", "roll_intent"}
                and
                command.expected_revision is not None
                and command.expected_revision != revision
            ):
                raise RevisionConflict(command.expected_revision, revision)

            result = self._apply(auth, command)
            if (
                command.type != "undo_encounter"
                and command.type not in NON_STATE_UNDO_COMMANDS
                and before_state != result.get("state")
                and (
                    before_state.get("encounter_status")
                    in {"active", "paused"}
                    or result["state"].get("encounter_status")
                    in {"active", "paused"}
                    or command.type in {"add_combatant", "start_encounter"}
                )
            ):
                self.store.push_encounter_undo(
                    auth.game_id,
                    auth.member_id,
                    command.type,
                    before_state,
                )
            if command.type not in {"roll", "roll_intent"}:
                revision = self.store.advance_revision(auth.game_id, revision)
            response = {
                **result,
                "revision": revision,
                "client_action_id": command.client_action_id,
                "replayed": False,
            }
            if command.client_action_id:
                self.store.save_command_receipt(
                    auth.game_id,
                    auth.member_id,
                    command.client_action_id,
                    command.type,
                    request_hash,
                    response,
                )
            return response

    @staticmethod
    def _request_hash(command: CommandRequest) -> str:
        canonical = json.dumps(
            {"type": command.type, "payload": command.payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _apply(self, auth: AuthContext, command: CommandRequest) -> dict:
        game = self.store.game(auth.game_id)
        state = deepcopy(game["state"])
        command_type, payload = command.type, command.payload
        event_visibility = "party"

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

        if command_type == "roll_intent":
            actor_character_id = payload.get("actor_character_id")
            if auth.role == "player":
                if not auth.character_id or actor_character_id != auth.character_id:
                    raise CommandError(
                        "Oyuncu typed roll actor olarak yalnizca kendi karakterini kullanabilir."
                    )
            elif (
                actor_character_id is not None
                and actor_character_id != auth.character_id
            ):
                self.require_active_dm(auth)
            if (
                actor_character_id is not None
                and actor_character_id not in state["characters"]
            ):
                raise CommandError("Typed roll actor karakteri bulunamadi.")

            dice = payload["dice"]
            count = int(dice["count"])
            sides = int(dice["sides"])
            modifier = int(dice["modifier"])
            mode = str(dice["mode"])
            if mode == "advantage":
                expression = f"2d20kh1{modifier:+d}"
            elif mode == "disadvantage":
                expression = f"2d20kl1{modifier:+d}"
            else:
                expression = f"{count}d{sides}{modifier:+d}"
            expression = expression.removesuffix("+0")
            try:
                result = roll(expression)
            except DiceError as error:
                raise CommandError(str(error)) from error

            requested_visibility = str(payload["visibility"])
            event_visibility = requested_visibility
            if requested_visibility in {"private", "dm_only"}:
                event_visibility = f"player:{auth.member_id}"
                if auth.role in {"dm", "co_dm"}:
                    event_visibility = "dm_only"
            privileged_context = event_visibility == "dm_only"
            intent = {
                "schema_version": 1,
                "intent_id": uuid4().hex,
                "actor": {
                    "member_id": auth.member_id,
                    "character_id": actor_character_id,
                },
                "action": {
                    "kind": "custom_roll",
                    "source_id": "global_fab",
                },
                "visibility": requested_visibility,
                "context": {
                    "surface": "global_fab",
                    "encounter_id": (
                        state.get("active_encounter_id")
                        if privileged_context
                        else None
                    ),
                    "round": state.get("round"),
                    "turn_index": (
                        state.get("turn_index")
                        if privileged_context
                        else None
                    ),
                },
                "roll": {
                    "count": count,
                    "sides": sides,
                    "modifier": modifier,
                    "mode": mode,
                    "expression": result.expression,
                },
            }
            event = self.store.add_event(
                auth.game_id,
                "typed_roll_resolved",
                auth.member_id,
                event_visibility,
                {
                    "intent": intent,
                    "roll": {
                        "expression": result.expression,
                        "rolls": result.rolls,
                        "kept": result.kept,
                        "modifier": result.modifier,
                        "total": result.total,
                    },
                },
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
                return self._apply(auth, nested)
            event = self.store.add_event(auth.game_id, "change_rejected", auth.member_id, "party", request)
            return {"event": event, "state": state}

        if command_type in {"apply_damage", "apply_heal"}:
            self.require_active_dm(auth)
            character = self.character_for(state, str(payload["character_id"]))
            amount = max(0, int(payload.get("amount", 0)))
            if command_type == "apply_damage":
                was_at_zero = character["hp"] == 0
                hp_before = character["hp"]
                absorbed = min(character.get("temp_hp", 0), amount)
                character["temp_hp"] -= absorbed
                effective = amount - absorbed
                character["hp"] = max(0, character["hp"] - effective)
                character, resource_result = self.resource_engine.apply_damage_state(
                    character,
                    amount,
                    critical=bool(payload.get("critical", False)),
                    was_at_zero=was_at_zero,
                    instant_death=max(0, effective - hp_before)
                    >= character["max_hp"],
                )
                if resource_result["death_status"] == "dead":
                    character = self.inventory_engine.end_all_attunement(character)
                state["characters"][character["id"]] = character
                event_type = "character_damaged"
            else:
                if (
                    character["resource_state"]["death_saves"]["status"]
                    == "dead"
                ):
                    raise CommandError(
                        "Olu karakter normal iyilestirme ile hayata dondurulemez."
                    )
                before = character["hp"]
                character["hp"] = min(character["max_hp"], character["hp"] + amount)
                effective = character["hp"] - before
                character = self.resource_engine.on_healed(character)
                state["characters"][character["id"]] = character
                resource_result = {
                    "death_status": character["resource_state"]["death_saves"]["status"]
                }
                event_type = "character_healed"
            event_payload = {"character_id": character["id"], "amount": amount, "effective": effective,
                             "hp": character["hp"], "temp_hp": character["temp_hp"],
                             **resource_result}

        elif command_type == "update_character":
            if auth.role != "player":
                self.require_active_dm(auth)
            character_id = (
                auth.character_id
                if auth.role == "player"
                else str(payload.get("character_id", ""))
            )
            if not character_id:
                raise CommandError("Karakter bulunamadi.")
            character = self.character_for(state, character_id)
            if auth.role == "player" and (
                "level" in payload or "class_id" in payload
            ):
                raise CommandError(
                    "Level ve class degisimi authoritative DM/typed builder islemi gerektirir."
                )
            if "level" in payload and int(payload["level"]) < int(character["level"]):
                raise CommandError(
                    "Generic character update ile level dusurulemez."
                )
            if (
                "class_id" in payload
                and payload["class_id"] != character.get("class_id")
            ):
                raise CommandError(
                    "Class degisimi typed builder islemi gerektirir."
                )
            protected_inputs = {"armor_class", "hit_points", "speed"}
            requested_inputs = payload.get("inputs", {})
            if protected_inputs & set(requested_inputs):
                raise CommandError(
                    "AC, HP ve speed politikalari manuel karakter guncellemesiyle "
                    "degistirilemez; typed rules islemi gereklidir."
                )
            try:
                state["characters"][character_id] = self.character_engine.update(
                    character,
                    {
                        key: value
                        for key, value in payload.items()
                        if key != "character_id"
                    },
                )
            except CharacterValidationError as error:
                raise CommandError(str(error)) from error
            recalculated = state["characters"][character_id]
            event_type, event_payload = "character_updated", {
                "character_id": character_id,
                "level": recalculated["level"],
            }

        elif command_type == "publish_character_draft":
            if state["encounter_status"] in {"active", "paused"}:
                raise CommandError(
                    "Devam eden encounter sirasinda character draft publish edilemez."
                )
            if auth.role == "player":
                character_id = auth.character_id
            else:
                self.require_active_dm(auth)
                character_id = str(payload.get("character_id", ""))
            if not character_id:
                raise CommandError("Karakter bulunamadi.")
            current_character = self.character_for(state, character_id)
            draft = self.store.character_draft(auth.game_id, character_id)
            if draft is None or draft["owner_id"] != current_character["owner_id"]:
                raise CommandError("Character draft bulunamadi.")
            draft_revision = int(payload["draft_revision"])
            if draft["revision"] != draft_revision or draft["status"] != "active":
                raise CommandError("Draft publish revision/status conflict.")
            if draft["current_step"] != "review":
                raise CommandError(
                    "Character draft publish icin review adiminda olmali."
                )
            try:
                self.store.character_draft_engine.validate_step(
                    draft["data"], "review", current_character["ruleset_version"]
                )
                published = self.store.character_draft_engine.build_character(
                    character_id,
                    current_character["owner_id"],
                    current_character["ruleset_version"],
                    draft["data"],
                )
                state["characters"][character_id] = published
                published_draft = self.store.mark_character_draft_published(
                    auth.game_id, character_id, draft_revision
                )
                self.store.mark_character_ready(auth.game_id, character_id)
            except CharacterDraftValidationError as error:
                raise CommandError(str(error)) from error
            event_type, event_payload = "character_draft_published", {
                "character_id": character_id,
                "draft_revision": published_draft["revision"],
            }

        elif command_type in {
            "configure_character_actions",
            "roll_character_check",
            "use_attack",
            "cast_spell",
        }:
            if auth.role == "player":
                character_id = auth.character_id
            else:
                self.require_active_dm(auth)
                character_id = str(payload.get("character_id", ""))
            if not character_id:
                raise CommandError("Karakter bulunamadi.")
            character = self.character_for(state, character_id)
            event_visibility = f"player:{character['owner_id']}"
            if state["encounter_status"] == "paused":
                raise CommandError(
                    "Duraklatilan encounter sirasinda action kullanilamaz."
                )
            try:
                if command_type == "configure_character_actions":
                    self.require_active_dm(auth)
                    character = self.action_engine.configure(
                        character,
                        ability=payload.get("ability"),
                        known_spell_ids=payload.get("known_spell_ids", []),
                        prepared_spell_ids=payload.get("prepared_spell_ids", []),
                        slots=payload.get("slots", {}),
                        attacks=payload.get("attacks", []),
                    )
                    state["characters"][character_id] = character
                    event_type, event_payload = "character_actions_configured", {
                        "character_id": character_id
                    }
                elif command_type == "roll_character_check":
                    resolved = self.action_engine.roll_check(
                        character,
                        str(payload["category"]),
                        str(payload["key"]),
                        str(payload.get("mode", "normal")),
                    )
                    event_type, event_payload = "character_check_rolled", {
                        "character_id": character_id,
                        **resolved,
                    }
                elif command_type == "use_attack":
                    target_id = str(payload["target_character_id"])
                    target = self.character_for(state, target_id)
                    if auth.role == "player" and target_id != character_id:
                        target_combatant = next(
                            (
                                item
                                for item in state["combatants"]
                                if item["id"] == target_id
                            ),
                            None,
                        )
                        if (
                            state["encounter_status"] != "active"
                            or target_combatant is None
                            or target_combatant.get("hidden", False)
                        ):
                            raise ActionValidationError(
                                "Baska bir karakter yalnizca gorunur aktif "
                                "encounter hedefiyse saldirilabilir."
                            )
                    self._require_turn_action(state, character_id, "action")
                    resolved = self.action_engine.attack(
                        character,
                        str(payload["attack_id"]),
                        target,
                        str(payload.get("mode", "normal")),
                    )
                    if resolved["hit"]:
                        amount = resolved["damage"]["total"]
                        target, damage_result = self._apply_damage(
                            target, amount, bool(resolved["critical"])
                        )
                        state["characters"][target_id] = target
                    else:
                        damage_result = None
                    self._consume_turn_action(
                        state, character_id, "action", f"attack:{payload['attack_id']}"
                    )
                    event_type, event_payload = "attack_resolved", {
                        "character_id": character_id,
                        "target_character_id": target_id,
                        **resolved,
                        "damage_result": damage_result,
                        "target_hp": target["hp"],
                    }
                else:
                    target_id = str(payload["target_character_id"])
                    target = self.character_for(state, target_id)
                    self._require_turn_action(state, character_id, "action")
                    character, resolved = self.action_engine.cast_spell(
                        character,
                        str(payload["spell_id"]),
                        int(payload["slot_level"]),
                        target,
                    )
                    if target_id == character_id:
                        target = character
                    if (
                        target["resource_state"]["death_saves"]["status"]
                        == "dead"
                    ):
                        raise ActionValidationError(
                            "Olu karakter normal iyilestirme ile hayata dondurulemez."
                        )
                    before = target["hp"]
                    target["hp"] = min(
                        target["max_hp"],
                        target["hp"] + resolved["healing"]["total"],
                    )
                    target = self.resource_engine.on_healed(target)
                    if target_id != character_id:
                        state["characters"][character_id] = character
                    state["characters"][target_id] = target
                    self._consume_turn_action(
                        state, character_id, "action", f"spell:{payload['spell_id']}"
                    )
                    event_type, event_payload = "spell_cast", {
                        "character_id": character_id,
                        "target_character_id": target_id,
                        **resolved,
                        "effective_healing": target["hp"] - before,
                        "target_hp": target["hp"],
                    }
            except (ActionValidationError, KeyError, DiceError) as error:
                raise CommandError(str(error)) from error

        elif command_type in {
            "add_inventory_item",
            "set_inventory_quantity",
            "remove_inventory_item",
            "move_inventory_item",
            "equip_item",
            "unequip_item",
            "attune_item",
            "unattune_item",
            "adjust_currency",
            "set_encumbrance_policy",
        }:
            if auth.role != "player":
                self.require_active_dm(auth)
            character_id = (
                auth.character_id
                if auth.role == "player"
                else str(payload.get("character_id", ""))
            )
            if not character_id:
                raise CommandError("Karakter bulunamadi.")
            if command_type == "set_encumbrance_policy":
                self.require_active_dm(auth)
            character = self.character_for(state, character_id)
            # Inventory contents and currency are private character-sheet data.
            # DM/co-DM can still view player-scoped events through store.can_view.
            event_visibility = f"player:{character['owner_id']}"
            if state["encounter_status"] == "paused" or (
                state["encounter_status"] == "active"
                and command_type not in {"equip_item", "unequip_item"}
            ):
                raise CommandError(
                    "Devam eden encounter sirasinda bu inventory islemi yapilamaz."
                )
            try:
                if command_type in {"equip_item", "unequip_item"} and state[
                    "encounter_status"
                ] == "active":
                    current = state["combatants"][state["turn_index"]]
                    if current["id"] != character_id:
                        raise InventoryValidationError(
                            "Equipment yalnizca karakterin aktif turunda degistirilebilir."
                        )
                    usage = state["turn_actions"].setdefault(character_id, {})
                    if usage.get("action"):
                        raise InventoryValidationError(
                            "Bu turdaki Action zaten kullanildi."
                        )

                if command_type == "add_inventory_item":
                    item_id = uuid4().hex
                    character = self.inventory_engine.add_item(
                        character,
                        item_id=item_id,
                        catalog_id=payload.get("catalog_id"),
                        name=payload.get("name"),
                        quantity=int(payload.get("quantity", 1)),
                        unit_weight_lb=payload.get("unit_weight_lb", 0),
                        unit_cost_gp=payload.get("unit_cost_gp", 0),
                        equipment_slot=payload.get("equipment_slot"),
                        armor_training=payload.get("armor_training"),
                        armor_class_bonus=payload.get("armor_class_bonus", 0),
                        container_capacity_lb=payload.get(
                            "container_capacity_lb"
                        ),
                        requires_attunement=payload.get(
                            "requires_attunement", False
                        ),
                        container_id=payload.get("container_id"),
                        allow_rules_fields=auth.role != "player",
                    )
                    event_type, event_payload = "inventory_item_added", {
                        "character_id": character_id,
                        "item_id": item_id,
                    }
                elif command_type == "remove_inventory_item":
                    item_id = str(payload["item_id"])
                    character = self.inventory_engine.remove_item(
                        character, item_id
                    )
                    event_type, event_payload = "inventory_item_removed", {
                        "character_id": character_id,
                        "item_id": item_id,
                    }
                elif command_type == "set_inventory_quantity":
                    item_id = str(payload["item_id"])
                    character = self.inventory_engine.set_quantity(
                        character, item_id, int(payload["quantity"])
                    )
                    event_type, event_payload = "inventory_quantity_changed", {
                        "character_id": character_id,
                        "item_id": item_id,
                        "quantity": payload["quantity"],
                    }
                elif command_type == "move_inventory_item":
                    item_id = str(payload["item_id"])
                    character = self.inventory_engine.move_item(
                        character, item_id, payload.get("container_id")
                    )
                    event_type, event_payload = "inventory_item_moved", {
                        "character_id": character_id,
                        "item_id": item_id,
                        "container_id": payload.get("container_id"),
                    }
                elif command_type in {"equip_item", "unequip_item"}:
                    item_id = str(payload["item_id"])
                    character = (
                        self.inventory_engine.equip(character, item_id)
                        if command_type == "equip_item"
                        else self.inventory_engine.unequip(character, item_id)
                    )
                    if state["encounter_status"] == "active":
                        state["turn_actions"][character_id]["action"] = (
                            f"inventory:{command_type}"
                        )
                    event_type, event_payload = (
                        "inventory_item_equipped"
                        if command_type == "equip_item"
                        else "inventory_item_unequipped"
                    ), {
                        "character_id": character_id,
                        "item_id": item_id,
                    }
                elif command_type in {"attune_item", "unattune_item"}:
                    if character["hp"] <= 0:
                        raise InventoryValidationError(
                            "Attunement rest'i icin en az 1 HP gerekir."
                        )
                    character, rest_result = self.resource_engine.short_rest(
                        character, 0
                    )
                    item_id = str(payload["item_id"])
                    character = (
                        self.inventory_engine.attune(character, item_id)
                        if command_type == "attune_item"
                        else self.inventory_engine.unattune(character, item_id)
                    )
                    event_type, event_payload = (
                        "inventory_item_attuned"
                        if command_type == "attune_item"
                        else "inventory_item_unattuned"
                    ), {
                        "character_id": character_id,
                        "item_id": item_id,
                        "rest": rest_result,
                    }
                elif command_type == "adjust_currency":
                    character = self.inventory_engine.adjust_currency(
                        character,
                        str(payload["denomination"]),
                        int(payload["delta"]),
                    )
                    event_type, event_payload = "currency_adjusted", {
                        "character_id": character_id,
                        "denomination": payload["denomination"],
                        "delta": payload["delta"],
                    }
                else:
                    character = self.inventory_engine.set_encumbrance_policy(
                        character, str(payload["policy"])
                    )
                    event_type, event_payload = "encumbrance_policy_changed", {
                        "character_id": character_id,
                        "policy": payload["policy"],
                    }
                character = self.character_engine.recalculate(character)
            except (InventoryValidationError, ResourceValidationError) as error:
                raise CommandError(str(error)) from error
            state["characters"][character_id] = character

        elif command_type in {
            "short_rest",
            "long_rest",
            "expend_resource",
            "use_second_wind",
            "death_save",
            "start_concentration",
            "end_concentration",
        }:
            if auth.role == "player":
                character_id = auth.character_id
            else:
                self.require_active_dm(auth)
                character_id = str(payload.get("character_id", ""))
            if not character_id:
                raise CommandError("Karakter bulunamadi.")
            character = self.character_for(state, character_id)
            if state["encounter_status"] == "paused":
                raise CommandError(
                    "Duraklatilan encounter sirasinda resource kullanilamaz."
                )
            if (
                state["encounter_status"] == "active"
                and state["combatants"]
                and state["combatants"][state["turn_index"]].get("kind")
                in {"lair", "environment"}
            ):
                raise CommandError(
                    "Environment turn sirasinda character resource kullanilamaz."
                )
            try:
                if command_type == "short_rest":
                    if state["encounter_status"] == "active":
                        raise ResourceValidationError(
                            "Aktif encounter sirasinda Short Rest yapilamaz."
                        )
                    character, rest_result = self.resource_engine.short_rest(
                        character, int(payload.get("hit_dice", 0))
                    )
                    event_type, event_payload = "short_rest_completed", {
                        "character_id": character_id,
                        **rest_result,
                    }
                elif command_type == "long_rest":
                    if state["encounter_status"] == "active":
                        raise ResourceValidationError(
                            "Aktif encounter sirasinda Long Rest yapilamaz."
                        )
                    character, rest_result = self.resource_engine.long_rest(character)
                    character, action_rest = self.action_engine.long_rest(character)
                    event_type, event_payload = "long_rest_completed", {
                        "character_id": character_id,
                        **rest_result,
                        **action_rest,
                    }
                elif command_type == "expend_resource":
                    resource_id = str(payload.get("resource_id", ""))
                    amount = int(payload.get("amount", 1))
                    character = self.resource_engine.expend_resource(
                        character, resource_id, amount
                    )
                    event_type, event_payload = "resource_expended", {
                        "character_id": character_id,
                        "resource_id": resource_id,
                        "amount": amount,
                    }
                elif command_type == "use_second_wind":
                    if state["encounter_status"] == "active":
                        current = state["combatants"][state["turn_index"]]
                        if current["id"] != character_id:
                            raise ResourceValidationError(
                                "Second Wind yalnizca karakterin aktif turunda kullanilabilir."
                            )
                        usage = state["turn_actions"].setdefault(character_id, {})
                        if usage.get("bonus_action"):
                            raise ResourceValidationError(
                                "Bu turdaki Bonus Action zaten kullanildi."
                            )
                    character, use_result = self.resource_engine.use_second_wind(
                        character
                    )
                    use_result["intent"] = self.action_engine.feature_intent(
                        character,
                        "feature:second-wind",
                        f"1d10{int(character['level']):+d}",
                    )
                    if state["encounter_status"] == "active":
                        state["turn_actions"][character_id]["bonus_action"] = (
                            "feature:second-wind"
                        )
                    event_type, event_payload = "second_wind_used", {
                        "character_id": character_id,
                        **use_result,
                    }
                elif command_type == "death_save":
                    if (
                        state["encounter_status"] != "active"
                        or not state["combatants"]
                        or state["combatants"][state["turn_index"]]["id"]
                        != character_id
                    ):
                        raise ResourceValidationError(
                            "Death Saving Throw yalnizca karakterin aktif turunda yapilabilir."
                        )
                    character, save_result = self.resource_engine.death_save(
                        character, int(state["turn_serial"])
                    )
                    if (
                        character["resource_state"]["death_saves"]["status"]
                        == "dead"
                    ):
                        character = self.inventory_engine.end_all_attunement(
                            character
                        )
                    event_type, event_payload = "death_save_rolled", {
                        "character_id": character_id,
                        "intent": {
                            "schema_version": 1,
                            "intent_id": uuid4().hex,
                            "kind": "save",
                            "actor_character_id": character_id,
                            "source_id": "death-save",
                            "action_cost": None,
                            "mode": "normal",
                            "roll": {"expression": "1d20", "modifier": 0},
                        },
                        **save_result,
                        **character["resource_state"]["death_saves"],
                    }
                elif command_type == "start_concentration":
                    character = self.resource_engine.start_concentration(
                        character,
                        str(payload.get("effect_id", "")),
                        str(payload.get("name", "")),
                    )
                    event_type, event_payload = "concentration_started", {
                        "character_id": character_id,
                        "effect_id": payload["effect_id"],
                    }
                else:
                    character = self.resource_engine.end_concentration(character)
                    event_type, event_payload = "concentration_ended", {
                        "character_id": character_id
                    }
            except (ResourceValidationError, ActionValidationError) as error:
                raise CommandError(str(error)) from error
            state["characters"][character_id] = character

        elif command_type in {"add_condition", "remove_condition"}:
            self.require_active_dm(auth)
            if state["encounter_status"] == "paused":
                raise CommandError(
                    "Duraklatilan encounter sirasinda condition degistirilemez."
                )
            character_id = str(payload.get("character_id", ""))
            character = self.character_for(state, character_id)
            condition_id = str(payload.get("condition_id", ""))
            try:
                if command_type == "add_condition":
                    character = self.resource_engine.add_condition(
                        character,
                        condition_id,
                        payload.get("duration", {"kind": "permanent"}),
                    )
                    event_type = "condition_added"
                else:
                    character = self.resource_engine.remove_condition(
                        character, condition_id
                    )
                    event_type = "condition_removed"
            except ResourceValidationError as error:
                raise CommandError(str(error)) from error
            state["characters"][character_id] = character
            event_payload = {
                "character_id": character_id,
                "condition_id": condition_id,
            }

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

        elif command_type in {
            "add_session_note",
            "add_session_loot",
            "claim_session_loot",
            "add_session_quest",
            "set_session_quest_status",
            "update_session_summary",
        }:
            if command_type == "add_session_note":
                note = self.store.add_session_note(
                    auth,
                    str(payload["content"]),
                    str(payload.get("visibility", "party")),
                )
                event_visibility = note["visibility"]
                event_type, event_payload = "session_note_added", {
                    "note_id": note["id"],
                    "content": note["content"],
                    "visibility": note["visibility"],
                }
            elif command_type == "claim_session_loot":
                loot = self.store.claim_session_loot(
                    auth.game_id, auth.member_id, str(payload["loot_id"])
                )
                event_type, event_payload = "session_loot_claimed", {
                    "loot_id": loot["id"],
                    "member_id": auth.member_id,
                    "name": loot["name"],
                }
            else:
                self.require_active_dm(auth)
                if command_type == "add_session_loot":
                    loot = self.store.add_session_loot(
                        auth.game_id,
                        auth.member_id,
                        str(payload["name"]),
                        int(payload.get("quantity", 1)),
                    )
                    event_type, event_payload = "session_loot_added", loot
                elif command_type == "add_session_quest":
                    quest = self.store.add_session_quest(
                        auth.game_id,
                        auth.member_id,
                        str(payload["title"]),
                        str(payload.get("description", "")),
                    )
                    event_type, event_payload = "session_quest_added", quest
                elif command_type == "set_session_quest_status":
                    quest = self.store.set_session_quest_status(
                        auth.game_id,
                        str(payload["quest_id"]),
                        str(payload["status"]),
                    )
                    event_type, event_payload = (
                        "session_quest_status_changed",
                        {
                            "quest_id": quest["id"],
                            "status": quest["status"],
                        },
                    )
                else:
                    summary = {
                        "schema_version": 1,
                        "title": str(payload.get("title", "")).strip(),
                        "highlights": list(payload.get("highlights", [])),
                        "next_steps": list(payload.get("next_steps", [])),
                        "published": bool(payload.get("published", False)),
                    }
                    session = self.store.update_session_summary(
                        auth.game_id, summary
                    )
                    event_visibility = (
                        "party" if summary["published"] else "dm_only"
                    )
                    event_type, event_payload = "session_summary_updated", {
                        "session_id": session["id"],
                        "published": summary["published"],
                        **(summary if summary["published"] else {}),
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

        elif command_type == "update_map_scene":
            self.require_active_dm(auth)
            scene_payload = {
                key: value
                for key, value in payload.items()
                if key != "scene_revision"
            }
            map_scene = self.store.update_map_scene(
                auth, int(payload["scene_revision"]), scene_payload
            )
            event_visibility = "party" if map_scene["published"] else "dm_only"
            event_type, event_payload = "map_scene_updated", {
                key: value
                for key, value in map_scene.items()
                if key != "tokens"
            }

        elif command_type in {
            "sync_map_tokens", "move_map_token", "remove_map_token",
        }:
            if command_type == "sync_map_tokens":
                self.require_active_dm(auth)
                tokens = self.store.sync_map_tokens(auth, state)
                event_visibility = "dm_only"
                event_type, event_payload = "map_tokens_synced", {
                    "token_count": len(tokens),
                }
            elif command_type == "move_map_token":
                if auth.role != "player":
                    self.require_active_dm(auth)
                try:
                    token = self.store.move_map_token(
                        auth,
                        str(payload["token_id"]),
                        int(payload["token_revision"]),
                        float(payload["x"]),
                        float(payload["y"]),
                    )
                except PermissionError as error:
                    raise CommandError(str(error)) from error
                combatant = next(
                    (
                        item for item in state.get("combatants", [])
                        if item.get("id") == token["combatant_id"]
                    ),
                    {},
                )
                scene_published = bool(
                    self.store.map_scene(auth)["published"]
                )
                event_visibility = (
                    "party"
                    if scene_published and not combatant.get("hidden", False)
                    else "dm_only"
                )
                event_type, event_payload = "map_token_moved", {
                    "token_id": token["id"],
                    "combatant_id": token["combatant_id"],
                    "x": token["x"],
                    "y": token["y"],
                    "token_revision": token["revision"],
                }
            else:
                self.require_active_dm(auth)
                token_id = str(payload["token_id"])
                current_token = next(
                    (
                        item for item in self.store.map_tokens(auth, state, True)
                        if item["id"] == token_id
                    ),
                    None,
                )
                if current_token is None:
                    raise CommandError("Map token bulunamadi.")
                combatant = next(
                    (
                        item for item in state.get("combatants", [])
                        if item.get("id") == current_token["combatant_id"]
                    ),
                    {},
                )
                scene_published = bool(
                    self.store.map_scene(auth)["published"]
                )
                self.store.remove_map_token(
                    auth, token_id, int(payload["token_revision"])
                )
                event_visibility = (
                    "party"
                    if scene_published and not combatant.get("hidden", False)
                    else "dm_only"
                )
                event_type, event_payload = "map_token_removed", {
                    "token_id": token_id,
                    "combatant_id": current_token["combatant_id"],
                }

        elif command_type in {"set_map_fog", "paint_map_fog"}:
            self.require_active_dm(auth)
            if command_type == "set_map_fog":
                fog_scene = self.store.set_map_fog(
                    auth,
                    int(payload["fog_revision"]),
                    bool(payload["enabled"]),
                )
                event_type, event_payload = "map_fog_toggled", {
                    "enabled": fog_scene["fog"]["enabled"],
                    "fog_revision": fog_scene["fog"]["revision"],
                }
            else:
                fog_scene = self.store.paint_map_fog(
                    auth,
                    int(payload["fog_revision"]),
                    str(payload["mode"]),
                    payload["cells"],
                )
                event_type, event_payload = "map_fog_painted", {
                    "mode": payload["mode"],
                    "cell_count": len(payload["cells"]),
                    "fog_revision": fog_scene["fog"]["revision"],
                }
            # The rasterized projection is delivered by snapshot/mask endpoint;
            # source fog cells never enter party events.
            event_visibility = "dm_only"

        elif command_type in {"map_ping", "map_draw"}:
            if command_type == "map_draw" or auth.role != "player":
                self.require_active_dm(auth)
            try:
                transient = self.store.create_map_transient(
                    auth,
                    "ping" if command_type == "map_ping" else "draw",
                    payload,
                )
            except PermissionError as error:
                raise CommandError(str(error)) from error
            scene = self.store.map_scene(auth)
            event_visibility = (
                "party"
                if (
                    scene["published"]
                    and not scene["fog"]["enabled"]
                )
                else "dm_only"
            )
            event_type, event_payload = (
                "map_pinged" if command_type == "map_ping" else "map_drawn",
                {
                    "transient_id": transient["id"],
                    "expires_at": transient["expires_at"],
                },
            )

        elif command_type in {
            "create_encounter_draft", "update_encounter_draft",
            "duplicate_encounter_draft", "start_saved_encounter",
            "pause_encounter", "resume_encounter",
        }:
            self.require_active_dm(auth)
            campaign_id = game["campaign_id"]
            if command_type == "create_encounter_draft":
                encounter = self.store.create_encounter_draft(
                    campaign_id,
                    auth.member_id,
                    str(payload["name"]),
                    str(payload.get("description", "")),
                )
                event_visibility = "dm_only"
                event_type, event_payload = "encounter_draft_created", {
                    "encounter_id": encounter["id"],
                    "name": encounter["data"]["name"],
                    "revision": encounter["revision"],
                }
            elif command_type == "update_encounter_draft":
                encounter = self.store.encounter_draft(
                    campaign_id, str(payload["encounter_id"])
                )
                if encounter is None:
                    raise CommandError("Encounter draft bulunamadi.")
                try:
                    updated_data = self.store.encounter_engine.patch(
                        encounter["data"], payload["patch"]
                    )
                    encounter = self.store.update_encounter_draft(
                        campaign_id,
                        encounter["id"],
                        int(payload["draft_revision"]),
                        updated_data,
                    )
                except EncounterDraftConflict:
                    raise
                except EncounterValidationError as error:
                    raise CommandError(str(error)) from error
                event_visibility = "dm_only"
                event_type, event_payload = "encounter_draft_updated", {
                    "encounter_id": encounter["id"],
                    "name": encounter["data"]["name"],
                    "revision": encounter["revision"],
                }
            elif command_type == "duplicate_encounter_draft":
                encounter = self.store.duplicate_encounter_draft(
                    campaign_id,
                    str(payload["encounter_id"]),
                    auth.member_id,
                )
                event_visibility = "dm_only"
                event_type, event_payload = "encounter_draft_duplicated", {
                    "encounter_id": encounter["id"],
                    "name": encounter["data"]["name"],
                    "revision": encounter["revision"],
                }
            elif command_type == "start_saved_encounter":
                if state["encounter_status"] in {"active", "paused"}:
                    raise CommandError(
                        "Canli encounter tamamlanmadan yenisi baslatilamaz."
                    )
                encounter = self.store.encounter_draft(
                    campaign_id, str(payload["encounter_id"])
                )
                if encounter is None:
                    raise CommandError("Encounter draft bulunamadi.")
                if encounter["revision"] != int(payload["draft_revision"]):
                    raise EncounterDraftConflict(
                        int(payload["draft_revision"]),
                        int(encounter["revision"]),
                    )
                try:
                    combatants = self.store.encounter_engine.hydrate(
                        encounter["data"], state["characters"]
                    )
                except EncounterValidationError as error:
                    raise CommandError(str(error)) from error
                if not combatants:
                    raise CommandError(
                        "Encounter baslatmak icin combatant ekleyin."
                    )
                for combatant in combatants:
                    combatant.setdefault("tie_breaker", 0)
                state.update(
                    combatants=combatants,
                    active_encounter_id=encounter["id"],
                    active_encounter_revision=encounter["revision"],
                    encounter_status="active",
                    round=1,
                    turn_index=0,
                    turn_serial=int(state.get("turn_serial", 0)) + 1,
                    turn_actions={},
                )
                event_type, event_payload = "encounter_started", {
                    "encounter_id": encounter["id"],
                    "name": encounter["data"]["name"],
                    "round": 1,
                    "combatant_count": len(combatants),
                }
            elif command_type == "pause_encounter":
                if state["encounter_status"] != "active":
                    raise CommandError("Yalniz aktif encounter duraklatilabilir.")
                state["encounter_status"] = "paused"
                event_type, event_payload = "encounter_paused", {
                    "encounter_id": state.get("active_encounter_id"),
                    "round": state["round"],
                    "turn_index": state["turn_index"],
                }
            else:
                if state["encounter_status"] != "paused":
                    raise CommandError(
                        "Yalniz duraklatilan encounter devam ettirilebilir."
                    )
                state["encounter_status"] = "active"
                event_type, event_payload = "encounter_resumed", {
                    "encounter_id": state.get("active_encounter_id"),
                    "round": state["round"],
                    "turn_index": state["turn_index"],
                }

        elif command_type in {
            "add_environment_entry", "set_initiative_tiebreaker",
            "adjust_combatant_hp", "undo_encounter",
        }:
            self.require_active_dm(auth)
            if command_type == "undo_encounter":
                undo = self.store.pop_encounter_undo(auth.game_id)
                state = undo["state"]
                event_visibility = "dm_only"
                event_type, event_payload = "encounter_undone", {
                    "undone_command": undo["command_type"],
                    "undo_id": undo["id"],
                }
            elif command_type == "add_environment_entry":
                if state["encounter_status"] not in {"active", "paused"}:
                    raise CommandError(
                        "Environment entry yalniz canli encounter'a eklenebilir."
                    )
                if len(state["combatants"]) >= 200:
                    raise CommandError("Encounter combatant limiti asildi.")
                current_id = (
                    state["combatants"][state["turn_index"]]["id"]
                    if state["combatants"] and state["turn_index"] >= 0
                    else None
                )
                entry = {
                    "id": uuid4().hex,
                    "source": {"type": "manual", "id": None},
                    "name": str(payload["name"]).strip(),
                    "kind": str(payload["kind"]),
                    "initiative": int(payload["initiative"]),
                    "tie_breaker": int(payload.get("tie_breaker", 0)),
                    "hidden": False,
                }
                state["combatants"].append(entry)
                self._sort_turn_order(state["combatants"])
                if current_id is not None:
                    state["turn_index"] = next(
                        index for index, item in enumerate(state["combatants"])
                        if item["id"] == current_id
                    )
                event_type, event_payload = "environment_entry_added", {
                    "entry_id": entry["id"],
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "initiative": entry["initiative"],
                }
            elif command_type == "set_initiative_tiebreaker":
                if state["encounter_status"] not in {"active", "paused"}:
                    raise CommandError("Canli encounter yok.")
                combatant_id = str(payload["combatant_id"])
                current_id = (
                    state["combatants"][state["turn_index"]]["id"]
                    if state["combatants"] and state["turn_index"] >= 0
                    else None
                )
                combatant = next(
                    (
                        item for item in state["combatants"]
                        if item["id"] == combatant_id
                    ),
                    None,
                )
                if combatant is None:
                    raise CommandError("Combatant bulunamadi.")
                combatant["tie_breaker"] = int(payload["tie_breaker"])
                self._sort_turn_order(state["combatants"])
                if current_id is not None:
                    state["turn_index"] = next(
                        index for index, item in enumerate(state["combatants"])
                        if item["id"] == current_id
                    )
                event_type, event_payload = "initiative_tie_resolved", {
                    "combatant_id": combatant_id,
                    "tie_breaker": combatant["tie_breaker"],
                }
                if combatant.get("hidden"):
                    event_visibility = "dm_only"
            else:
                if state["encounter_status"] != "active":
                    raise CommandError(
                        "Combatant HP yalniz aktif encounter sirasinda degistirilebilir."
                    )
                combatant_id = str(payload["combatant_id"])
                delta = int(payload["delta"])
                combatant = next(
                    (
                        item for item in state["combatants"]
                        if item["id"] == combatant_id
                    ),
                    None,
                )
                if combatant is None:
                    raise CommandError("Combatant bulunamadi.")
                character = state["characters"].get(combatant_id)
                effect = None
                if character is not None:
                    if delta < 0:
                        character, effect = self._apply_damage(
                            character, -delta
                        )
                    else:
                        if (
                            character["resource_state"]["death_saves"]["status"]
                            == "dead"
                        ):
                            raise CommandError(
                                "Olu karakter normal iyilestirme ile hayata dondurulemez."
                            )
                        before = character["hp"]
                        character["hp"] = min(
                            character["max_hp"], character["hp"] + delta
                        )
                        character = self.resource_engine.on_healed(character)
                        effect = {"effective_healing": character["hp"] - before}
                    state["characters"][combatant_id] = character
                else:
                    if (
                        not isinstance(combatant.get("hp"), int)
                        or not isinstance(combatant.get("max_hp"), int)
                    ):
                        raise CommandError(
                            "Environment entry HP tasimaz."
                        )
                    combatant["hp"] = min(
                        int(combatant["max_hp"]),
                        max(0, int(combatant["hp"]) + delta),
                    )
                event_type, event_payload = "combatant_hp_adjusted", {
                    "combatant_id": combatant_id,
                    "delta": delta,
                    **({"character_effect": effect} if effect else {}),
                }
                if combatant.get("hidden"):
                    event_visibility = "dm_only"

        elif command_type == "add_combatant":
            self.require_active_dm(auth)
            if state["encounter_status"] in {"active", "paused"}:
                raise CommandError(
                    "Canli encounter listesi builder disindan degistirilemez."
                )
            if len(state["combatants"]) >= 200:
                raise CommandError("Encounter combatant limiti asildi.")
            combatant_id = str(
                payload.get("id") or payload.get("character_id") or uuid4().hex
            )
            if any(item["id"] == combatant_id for item in state["combatants"]):
                raise CommandError("Combatant zaten initiative listesinde.")
            state["combatants"].append({
                "id": combatant_id,
                "source": {
                    "type": (
                        "character"
                        if combatant_id in state["characters"]
                        else "manual"
                    ),
                    "id": (
                        combatant_id
                        if combatant_id in state["characters"]
                        else None
                    ),
                },
                "name": str(payload["name"]), "initiative": int(payload.get("initiative", 0)),
                "tie_breaker": int(payload.get("tie_breaker", 0)),
                "hp": payload.get("hp"), "max_hp": payload.get("hp"),
                "kind": str(payload.get("kind", "monster")),
                "hidden": bool(payload.get("hidden", False)),
            })
            event_type, event_payload = "combatant_added", {"name": payload["name"]}

        elif command_type == "start_encounter":
            self.require_active_dm(auth)
            if state["encounter_status"] in {"active", "paused"}:
                raise CommandError("Encounter zaten aktif veya duraklatildi.")
            if not state["combatants"]:
                raise CommandError("Encounter icin katilimci ekleyin.")
            self._sort_turn_order(state["combatants"])
            state.update(
                encounter_status="active",
                active_encounter_id=None,
                active_encounter_revision=None,
                round=1,
                turn_index=0,
                turn_serial=int(state.get("turn_serial", 0)) + 1,
                turn_actions={},
            )
            event_type, event_payload = "encounter_started", {"round": 1}

        elif command_type == "next_turn":
            self.require_active_dm(auth)
            if state["encounter_status"] != "active":
                raise CommandError("Aktif encounter yok.")
            ending_combatant = state["combatants"][state["turn_index"]]
            expired_conditions = []
            ending_character = state["characters"].get(ending_combatant["id"])
            if ending_character is not None:
                ending_character, expired_conditions = self.resource_engine.tick_end_turn(
                    ending_character
                )
                state["characters"][ending_combatant["id"]] = ending_character
            state["turn_index"] += 1
            state["turn_serial"] += 1
            state["turn_actions"] = {}
            if state["turn_index"] >= len(state["combatants"]):
                state["turn_index"] = 0
                state["round"] += 1
            event_type, event_payload = "turn_advanced", {
                "round": state["round"], "turn_index": state["turn_index"],
                "expired_conditions": expired_conditions,
            }

        elif command_type == "complete_encounter":
            self.require_active_dm(auth)
            if state["encounter_status"] not in {"active", "paused"}:
                raise CommandError("Tamamlanabilecek canli encounter yok.")
            state["encounter_status"] = "completed"
            state["turn_actions"] = {}
            event_type, event_payload = "encounter_completed", {
                "encounter_id": state.get("active_encounter_id"),
                "round": state["round"],
            }

        else:
            raise CommandError("Desteklenmeyen komut.")

        self._sync_character_combatants(state)
        self.store.save_state(auth.game_id, state)
        event = self.store.add_event(
            auth.game_id,
            event_type,
            auth.member_id,
            event_visibility,
            event_payload,
        )
        return {"event": event, "state": state}

    @staticmethod
    def _sort_turn_order(combatants: list[dict]) -> None:
        combatants.sort(
            key=lambda item: (
                -int(item.get("initiative", 0)),
                -int(item.get("tie_breaker", 0)),
                str(item.get("name", "")).casefold(),
                str(item.get("id", "")),
            )
        )

    @staticmethod
    def _sync_character_combatants(state: dict) -> None:
        for combatant in state.get("combatants", []):
            character = state.get("characters", {}).get(combatant.get("id"))
            if character is None:
                continue
            combatant.update(
                name=character["name"],
                hp=int(character["hp"]),
                max_hp=int(character["max_hp"]),
                armor_class=int(character["ac"]),
            )

    @staticmethod
    def _require_turn_action(state: dict, character_id: str, cost: str) -> None:
        if state["encounter_status"] != "active":
            return
        current = state["combatants"][state["turn_index"]]
        if current["id"] != character_id:
            raise ActionValidationError(
                "Action yalnizca karakterin aktif turunda kullanilabilir."
            )
        if state["turn_actions"].setdefault(character_id, {}).get(cost):
            raise ActionValidationError(f"Bu turdaki {cost} zaten kullanildi.")

    @staticmethod
    def _consume_turn_action(
        state: dict, character_id: str, cost: str, source_id: str
    ) -> None:
        if state["encounter_status"] == "active":
            state["turn_actions"].setdefault(character_id, {})[cost] = source_id

    def _apply_damage(
        self, character: dict, amount: int, critical: bool = False
    ) -> tuple[dict, dict]:
        result = deepcopy(character)
        was_at_zero = result["hp"] == 0
        hp_before = result["hp"]
        absorbed = min(result.get("temp_hp", 0), amount)
        result["temp_hp"] -= absorbed
        effective = amount - absorbed
        result["hp"] = max(0, result["hp"] - effective)
        result, resource_result = self.resource_engine.apply_damage_state(
            result,
            amount,
            critical=critical,
            was_at_zero=was_at_zero,
            instant_death=max(0, effective - hp_before) >= result["max_hp"],
        )
        if resource_result["death_status"] == "dead":
            result = self.inventory_engine.end_all_attunement(result)
        concentration = resource_result.get("concentration_check")
        if concentration is not None and concentration.get("roll") is not None:
            modifier = int(concentration["modifier"])
            concentration["intent"] = {
                "schema_version": 1,
                "intent_id": uuid4().hex,
                "kind": "save",
                "actor_character_id": result["id"],
                "source_id": "concentration",
                "action_cost": None,
                "mode": "normal",
                "roll": {
                    "expression": f"1d20{modifier:+d}".removesuffix("+0"),
                    "modifier": modifier,
                },
                "dc": concentration["dc"],
            }
        return result, resource_result


