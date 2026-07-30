from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DMMode = Literal["human", "assisted", "ai"]
Role = Literal["dm", "co_dm", "player"]


class UpdateDicePreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Literal["crimson", "arcane", "ivory"]
    sound_enabled: Annotated[bool, Field(strict=True)]


class CreateGameRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dm_name: Annotated[str, Field(min_length=1, max_length=40)]
    dm_mode: DMMode = "human"


class JoinGameRequest(BaseModel):
    invite_code: Annotated[str, Field(min_length=6, max_length=64)]
    player_name: Annotated[str, Field(min_length=1, max_length=40)]


class CreateSessionRequest(BaseModel):
    title: Annotated[str, Field(max_length=120)] | None = None


class UpdateSessionStatusRequest(BaseModel):
    status: Literal["live", "paused", "completed"]
    expected_revision: Annotated[int, Field(ge=0)]


class SaveCharacterDraftRequest(BaseModel):
    expected_revision: Annotated[int, Field(ge=1)]
    patch: dict


class NavigateCharacterDraftRequest(BaseModel):
    expected_revision: Annotated[int, Field(ge=1)]
    direction: Literal["next", "previous"]


class CloneRulesetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_version: Annotated[
        str, Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")
    ]
    version: Annotated[
        str, Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")
    ]
    name: Annotated[str, Field(min_length=1, max_length=120)]


class SaveCatalogEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)]
    entry: dict


class DeleteCatalogEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)]


class PublishRulesetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)]
    make_default: bool = False


class HouseRuleInput(BaseModel):
    id: Annotated[str, Field(min_length=1, max_length=64)]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=1000)] = ""
    enabled: bool = True


class UpdateCampaignSettingsRequest(BaseModel):
    expected_version: Annotated[int, Field(ge=1)]
    house_rules: Annotated[list[HouseRuleInput], Field(max_length=50)]
    safety_tools: Annotated[
        list[Literal["x_card", "lines_veils", "open_door", "stars_wishes"]],
        Field(max_length=4),
    ]
    session_zero_agenda: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=240)]],
        Field(max_length=30),
    ]

    @model_validator(mode="after")
    def unique_settings_entries(self):
        rule_ids = [rule.id for rule in self.house_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("House rule id degerleri benzersiz olmali.")
        if len(self.safety_tools) != len(set(self.safety_tools)):
            raise ValueError("Safety tool degerleri tekrar edemez.")
        return self


class UpdateSessionZeroMemberRequest(BaseModel):
    expected_version: Annotated[int, Field(ge=1)]
    readiness_status: Literal["not_ready", "ready"]
    consent_status: Literal["pending", "accepted", "declined"]
    lines: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=240)]],
        Field(max_length=50),
    ] = Field(default_factory=list)
    veils: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=240)]],
        Field(max_length=50),
    ] = Field(default_factory=list)
    notes: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def ready_requires_consent(self):
        if self.readiness_status == "ready" and self.consent_status != "accepted":
            raise ValueError("Ready olmak icin Session Zero onayi gerekir.")
        return self


class ScheduleSessionRequest(BaseModel):
    expected_revision: Annotated[int, Field(ge=0)]
    scheduled_at: datetime | None

    @model_validator(mode="after")
    def scheduled_time_requires_timezone(self):
        if (
            self.scheduled_at is not None
            and self.scheduled_at.utcoffset() is None
        ):
            raise ValueError("Planlanan oturum tarihi timezone icermeli.")
        return self


class RotateInviteRequest(BaseModel):
    max_uses: Annotated[int, Field(ge=1, le=500)] = 50


class DeleteCampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Annotated[str, Field(min_length=1, max_length=200)]


class CommandRequest(BaseModel):
    type: Literal[
        "roll", "roll_intent", "request_damage", "request_heal", "approve_request",
        "reject_request", "apply_damage", "apply_heal", "set_dm_mode",
        "add_combatant", "start_encounter", "next_turn", "complete_encounter",
        "update_character", "update_scene",
        "assign_co_dm", "remove_co_dm", "set_fallback_mode",
        "accept_dm_handover", "vote_ai_takeover", "reclaim_dm_control",
        "short_rest", "long_rest", "expend_resource", "use_second_wind",
        "death_save",
        "start_concentration", "end_concentration", "add_condition",
        "remove_condition",
        "add_inventory_item", "set_inventory_quantity",
        "remove_inventory_item", "move_inventory_item", "equip_item",
        "unequip_item", "attune_item", "unattune_item",
        "adjust_currency", "set_encumbrance_policy",
        "configure_character_actions", "roll_character_check",
        "use_attack", "cast_spell",
        "publish_character_draft",
        "add_session_note", "add_session_loot", "claim_session_loot",
        "add_session_quest", "set_session_quest_status",
        "update_session_summary",
        "create_encounter_draft", "update_encounter_draft",
        "duplicate_encounter_draft", "start_saved_encounter",
        "pause_encounter", "resume_encounter",
        "add_environment_entry", "set_initiative_tiebreaker",
        "adjust_combatant_hp", "undo_encounter",
        "update_map_scene",
        "sync_map_tokens", "move_map_token", "remove_map_token",
        "set_map_fog", "paint_map_fog",
        "map_ping", "map_draw",
    ]
    payload: dict = Field(default_factory=dict)
    client_action_id: Annotated[str, Field(min_length=8, max_length=64)] | None = None
    expected_revision: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        payload = self.payload
        if self.type == "roll":
            expression = str(payload.get("expression", "1d20"))
            if len(expression) > 100:
                raise ValueError("Zar ifadesi en fazla 100 karakter olabilir.")
            visibility = payload.get("visibility", "public")
            if visibility not in {"public", "party", "dm_only"}:
                raise ValueError("Gecersiz zar gorunurlugu.")
        if self.type == "roll_intent":
            if set(payload) != {
                "actor_character_id", "action", "visibility", "context", "dice"
            }:
                raise ValueError("Typed roll payload alanlari gecersiz.")
            actor_character_id = payload.get("actor_character_id")
            if actor_character_id is not None and (
                not isinstance(actor_character_id, str)
                or not 1 <= len(actor_character_id) <= 64
            ):
                raise ValueError("Typed roll actor gecersiz.")
            if payload.get("action") != "custom_roll":
                raise ValueError("Typed roll action gecersiz.")
            if payload.get("visibility") not in {"party", "private", "dm_only"}:
                raise ValueError("Typed roll visibility gecersiz.")
            if payload.get("context") != "global_fab":
                raise ValueError("Typed roll context gecersiz.")
            dice = payload.get("dice")
            if not isinstance(dice, dict) or set(dice) != {
                "count", "sides", "modifier", "mode"
            }:
                raise ValueError("Typed roll dice yapisi gecersiz.")
            count = dice.get("count")
            sides = dice.get("sides")
            modifier = dice.get("modifier")
            mode = dice.get("mode")
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or not 1 <= count <= 100
            ):
                raise ValueError("Typed roll count gecersiz.")
            if sides not in {4, 6, 8, 10, 12, 20, 100}:
                raise ValueError("Typed roll sides gecersiz.")
            if (
                not isinstance(modifier, int)
                or isinstance(modifier, bool)
                or not -99_999 <= modifier <= 99_999
            ):
                raise ValueError("Typed roll modifier gecersiz.")
            if mode not in {"normal", "advantage", "disadvantage"}:
                raise ValueError("Typed roll mode gecersiz.")
            if mode != "normal" and (count != 2 or sides != 20):
                raise ValueError(
                    "Avantaj/dezavantaj tam olarak iki d20 gerektirir."
                )
        if self.type == "update_map_scene":
            allowed = {
                "scene_revision", "asset_id", "name", "grid_type",
                "grid_size_px", "distance_per_cell", "distance_unit",
                "viewport", "published",
            }
            if set(payload) != allowed:
                raise ValueError("Map scene payload alanlari gecersiz.")
            scene_revision = payload.get("scene_revision")
            if (
                not isinstance(scene_revision, int)
                or isinstance(scene_revision, bool)
                or scene_revision < 1
            ):
                raise ValueError("Map scene revision gecersiz.")
            asset_id = payload.get("asset_id")
            if asset_id is not None and (
                not isinstance(asset_id, str)
                or not 8 <= len(asset_id) <= 64
            ):
                raise ValueError("Map asset id gecersiz.")
            name = payload.get("name")
            if (
                not isinstance(name, str)
                or not 1 <= len(name.strip()) <= 120
            ):
                raise ValueError("Map scene adi gecersiz.")
            if payload.get("grid_type") not in {"none", "square", "hex"}:
                raise ValueError("Map grid tipi gecersiz.")
            grid_size = payload.get("grid_size_px")
            if (
                not isinstance(grid_size, int)
                or isinstance(grid_size, bool)
                or not 10 <= grid_size <= 512
            ):
                raise ValueError("Map grid boyutu gecersiz.")
            distance = payload.get("distance_per_cell")
            if (
                not isinstance(distance, (int, float))
                or isinstance(distance, bool)
                or not 0.1 <= float(distance) <= 1000
            ):
                raise ValueError("Map grid olcegi gecersiz.")
            if payload.get("distance_unit") not in {"ft", "m"}:
                raise ValueError("Map mesafe birimi gecersiz.")
            viewport = payload.get("viewport")
            if not isinstance(viewport, dict) or set(viewport) != {
                "x", "y", "zoom"
            }:
                raise ValueError("Map viewport gecersiz.")
            for key, minimum, maximum in (
                ("x", -100_000, 100_000),
                ("y", -100_000, 100_000),
                ("zoom", 0.1, 8),
            ):
                value = viewport.get(key)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not minimum <= float(value) <= maximum
                ):
                    raise ValueError("Map viewport degeri gecersiz.")
            if not isinstance(payload.get("published"), bool):
                raise ValueError("Map published boolean olmali.")
            if payload["published"] and asset_id is None:
                raise ValueError("Map publish icin asset gereklidir.")
        if self.type == "sync_map_tokens" and payload:
            raise ValueError("Map token sync payload bos olmali.")
        if self.type in {"move_map_token", "remove_map_token"}:
            allowed = (
                {"token_id", "token_revision", "x", "y"}
                if self.type == "move_map_token"
                else {"token_id", "token_revision"}
            )
            if set(payload) != allowed:
                raise ValueError("Map token payload alanlari gecersiz.")
            token_id = payload.get("token_id")
            if (
                not isinstance(token_id, str)
                or not 8 <= len(token_id) <= 64
            ):
                raise ValueError("Map token id gecersiz.")
            revision = payload.get("token_revision")
            if (
                not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision < 1
            ):
                raise ValueError("Map token revision gecersiz.")
            if self.type == "move_map_token":
                for field in ("x", "y"):
                    value = payload.get(field)
                    if (
                        not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        or not 0 <= float(value) <= 100_000
                    ):
                        raise ValueError("Map token koordinati gecersiz.")
        if self.type == "set_map_fog":
            if set(payload) != {"fog_revision", "enabled"}:
                raise ValueError("Map fog payload alanlari gecersiz.")
            if (
                not isinstance(payload.get("fog_revision"), int)
                or isinstance(payload.get("fog_revision"), bool)
                or payload["fog_revision"] < 1
                or not isinstance(payload.get("enabled"), bool)
            ):
                raise ValueError("Map fog ayari gecersiz.")
        if self.type == "paint_map_fog":
            if set(payload) != {"fog_revision", "mode", "cells"}:
                raise ValueError("Map fog paint payload alanlari gecersiz.")
            if (
                not isinstance(payload.get("fog_revision"), int)
                or isinstance(payload.get("fog_revision"), bool)
                or payload["fog_revision"] < 1
                or payload.get("mode") not in {"reveal", "hide"}
            ):
                raise ValueError("Map fog paint ayari gecersiz.")
            cells = payload.get("cells")
            if (
                not isinstance(cells, list)
                or not 1 <= len(cells) <= 512
                or any(
                    not isinstance(cell, list)
                    or len(cell) != 2
                    or any(
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or not 0 <= value <= 8191
                        for value in cell
                    )
                    for cell in cells
                )
                or len({tuple(cell) for cell in cells}) != len(cells)
            ):
                raise ValueError("Map fog cell listesi gecersiz.")
        if self.type == "map_ping":
            if set(payload) != {"x", "y"}:
                raise ValueError("Map ping payload alanlari gecersiz.")
            for field in ("x", "y"):
                value = payload.get(field)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not 0 <= float(value) <= 100_000
                ):
                    raise ValueError("Map ping koordinati gecersiz.")
        if self.type == "map_draw":
            if set(payload) != {"points"}:
                raise ValueError("Map draw payload alanlari gecersiz.")
            points = payload.get("points")
            if (
                not isinstance(points, list)
                or not 2 <= len(points) <= 64
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
                raise ValueError("Map draw point listesi gecersiz.")

        if self.type in {"request_damage", "request_heal", "apply_damage", "apply_heal"}:
            amount = payload.get("amount", 0)
            if isinstance(amount, bool):
                raise ValueError("Miktar bir tam sayi olmalidir.")
            try:
                amount = int(amount)
            except (TypeError, ValueError) as error:
                raise ValueError("Miktar bir tam sayi olmalidir.") from error
            if not 0 <= amount <= 1_000_000:
                raise ValueError("Miktar 0 ile 1000000 arasinda olmalidir.")
            if self.type == "apply_damage" and "critical" in payload and not isinstance(
                payload["critical"], bool
            ):
                raise ValueError("critical boolean olmali.")

        if self.type in {"approve_request", "reject_request"}:
            request_id = payload.get("request_id")
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
                raise ValueError("Gecerli bir request_id gereklidir.")

        inventory_item_commands = {
            "set_inventory_quantity",
            "remove_inventory_item",
            "move_inventory_item",
            "equip_item",
            "unequip_item",
            "attune_item",
            "unattune_item",
        }
        inventory_allowed = {
            "add_inventory_item": {
                "character_id",
                "catalog_id",
                "name",
                "quantity",
                "unit_weight_lb",
                "unit_cost_gp",
                "equipment_slot",
                "armor_training",
                "armor_class_bonus",
                "container_capacity_lb",
                "container_id",
                "requires_attunement",
            },
            "set_inventory_quantity": {
                "character_id",
                "item_id",
                "quantity",
            },
            "remove_inventory_item": {"character_id", "item_id"},
            "move_inventory_item": {
                "character_id",
                "item_id",
                "container_id",
            },
            "equip_item": {"character_id", "item_id"},
            "unequip_item": {"character_id", "item_id"},
            "attune_item": {"character_id", "item_id"},
            "unattune_item": {"character_id", "item_id"},
            "adjust_currency": {
                "character_id",
                "denomination",
                "delta",
            },
            "set_encumbrance_policy": {"character_id", "policy"},
        }
        if self.type in inventory_allowed:
            unknown = set(payload) - inventory_allowed[self.type]
            if unknown:
                raise ValueError("Bilinmeyen inventory payload alani.")
        if self.type in inventory_item_commands:
            item_id = payload.get("item_id")
            if not isinstance(item_id, str) or not 8 <= len(item_id) <= 64:
                raise ValueError("item_id gecersiz.")
        if self.type == "add_inventory_item":
            catalog_id = payload.get("catalog_id")
            name = payload.get("name")
            if catalog_id is not None and (
                not isinstance(catalog_id, str) or not 1 <= len(catalog_id) <= 80
            ):
                raise ValueError("catalog_id gecersiz.")
            if catalog_id is None and (
                not isinstance(name, str) or not 1 <= len(name.strip()) <= 120
            ):
                raise ValueError("Custom item adi gecersiz.")
        if self.type in {"add_inventory_item", "set_inventory_quantity"}:
            quantity = payload.get("quantity", 1)
            if (
                not isinstance(quantity, int)
                or isinstance(quantity, bool)
                or not 1 <= quantity <= 1_000_000
            ):
                raise ValueError("quantity 1..1000000 arasinda olmali.")
        if self.type == "move_inventory_item":
            container_id = payload.get("container_id")
            if container_id is not None and (
                not isinstance(container_id, str)
                or not 8 <= len(container_id) <= 64
            ):
                raise ValueError("container_id gecersiz.")
        if self.type == "adjust_currency":
            if payload.get("denomination") not in {"cp", "sp", "ep", "gp", "pp"}:
                raise ValueError("Currency denomination gecersiz.")
            delta = payload.get("delta")
            if (
                not isinstance(delta, int)
                or isinstance(delta, bool)
                or not -1_000_000_000 <= delta <= 1_000_000_000
            ):
                raise ValueError("Currency delta gecersiz.")
        if self.type == "set_encumbrance_policy" and payload.get("policy") not in {
            "standard",
            "ignore",
        }:
            raise ValueError("Encumbrance policy gecersiz.")

        action_allowed = {
            "configure_character_actions": {
                "character_id", "ability", "known_spell_ids",
                "prepared_spell_ids", "slots", "attacks",
            },
            "roll_character_check": {"character_id", "category", "key", "mode"},
            "use_attack": {
                "character_id", "attack_id", "target_character_id", "mode"
            },
            "cast_spell": {
                "character_id", "spell_id", "slot_level", "target_character_id"
            },
        }
        if self.type in action_allowed:
            if set(payload) - action_allowed[self.type]:
                raise ValueError("Bilinmeyen action payload alani.")
            character_id = payload.get("character_id")
            if character_id is not None and (
                not isinstance(character_id, str)
                or not 1 <= len(character_id) <= 64
            ):
                raise ValueError("character_id gecersiz.")
        if self.type == "configure_character_actions":
            if payload.get("ability") not in {
                None, "strength", "dexterity", "constitution",
                "intelligence", "wisdom", "charisma",
            }:
                raise ValueError("Spellcasting ability gecersiz.")
            for field in ("known_spell_ids", "prepared_spell_ids", "attacks"):
                if not isinstance(payload.get(field, []), list):
                    raise ValueError(f"{field} liste olmali.")
            if (
                len(payload.get("known_spell_ids", [])) > 500
                or len(payload.get("prepared_spell_ids", [])) > 500
                or len(payload.get("attacks", [])) > 100
            ):
                raise ValueError("Action yapilandirma limiti asildi.")
            if not isinstance(payload.get("slots", {}), dict):
                raise ValueError("slots obje olmali.")
        if self.type == "roll_character_check":
            if payload.get("category") not in {"ability", "skill", "save"}:
                raise ValueError("Roll category gecersiz.")
            if (
                not isinstance(payload.get("key"), str)
                or not 1 <= len(payload["key"]) <= 40
            ):
                raise ValueError("Roll key gecersiz.")
        if self.type in {"roll_character_check", "use_attack"} and payload.get(
            "mode", "normal"
        ) not in {"normal", "advantage", "disadvantage"}:
            raise ValueError("Roll mode gecersiz.")
        if self.type == "use_attack":
            attack_id = payload.get("attack_id")
            target_id = payload.get("target_character_id")
            if not isinstance(attack_id, str) or not 1 <= len(attack_id) <= 80:
                raise ValueError("attack_id gecersiz.")
            if not isinstance(target_id, str) or not 1 <= len(target_id) <= 64:
                raise ValueError("target_character_id gecersiz.")
        if self.type == "cast_spell":
            spell_id = payload.get("spell_id")
            target_id = payload.get("target_character_id")
            if not isinstance(spell_id, str) or not 1 <= len(spell_id) <= 80:
                raise ValueError("spell_id gecersiz.")
            if not isinstance(target_id, str) or not 1 <= len(target_id) <= 64:
                raise ValueError("target_character_id gecersiz.")
            slot_level = payload.get("slot_level")
            if (
                not isinstance(slot_level, int)
                or isinstance(slot_level, bool)
                or not 1 <= slot_level <= 9
            ):
                raise ValueError("slot_level 1..9 arasinda olmali.")
        if self.type == "publish_character_draft":
            unknown = set(payload) - {"character_id", "draft_revision"}
            if unknown:
                raise ValueError("Bilinmeyen draft publish alani.")
            revision = payload.get("draft_revision")
            if (
                not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision < 1
            ):
                raise ValueError("draft_revision gecersiz.")
        session_allowed = {
            "add_session_note": {"content", "visibility"},
            "add_session_loot": {"name", "quantity"},
            "claim_session_loot": {"loot_id"},
            "add_session_quest": {"title", "description"},
            "set_session_quest_status": {"quest_id", "status"},
            "update_session_summary": {
                "title", "highlights", "next_steps", "published"
            },
        }
        if self.type in session_allowed:
            if set(payload) - session_allowed[self.type]:
                raise ValueError("Bilinmeyen session workspace alani.")
        if self.type == "add_session_note":
            content = payload.get("content")
            if not isinstance(content, str) or not 1 <= len(content.strip()) <= 4000:
                raise ValueError("Session note 1..4000 karakter olmali.")
            if payload.get("visibility", "party") not in {
                "party", "dm_only", "private"
            }:
                raise ValueError("Session note visibility gecersiz.")
        if self.type == "add_session_loot":
            name = payload.get("name")
            quantity = payload.get("quantity", 1)
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
                raise ValueError("Loot adi gecersiz.")
            if (
                not isinstance(quantity, int)
                or isinstance(quantity, bool)
                or not 1 <= quantity <= 1_000_000
            ):
                raise ValueError("Loot quantity gecersiz.")
        if self.type in {"claim_session_loot", "set_session_quest_status"}:
            id_field = "loot_id" if self.type == "claim_session_loot" else "quest_id"
            value = payload.get(id_field)
            if not isinstance(value, str) or not 8 <= len(value) <= 64:
                raise ValueError(f"{id_field} gecersiz.")
        if self.type == "add_session_quest":
            title = payload.get("title")
            description = payload.get("description", "")
            if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
                raise ValueError("Quest title gecersiz.")
            if not isinstance(description, str) or len(description) > 2000:
                raise ValueError("Quest description gecersiz.")
        if self.type == "set_session_quest_status" and payload.get("status") not in {
            "active", "completed", "failed"
        }:
            raise ValueError("Quest status gecersiz.")
        if self.type == "update_session_summary":
            if not isinstance(payload.get("title", ""), str) or len(
                payload.get("title", "")
            ) > 160:
                raise ValueError("Summary title gecersiz.")
            for field in ("highlights", "next_steps"):
                values = payload.get(field, [])
                if (
                    not isinstance(values, list)
                    or len(values) > 50
                    or any(
                        not isinstance(value, str)
                        or not 1 <= len(value) <= 500
                        for value in values
                    )
                ):
                    raise ValueError(f"Summary {field} gecersiz.")
            if not isinstance(payload.get("published", False), bool):
                raise ValueError("Summary published boolean olmali.")
        encounter_allowed = {
            "create_encounter_draft": {"name", "description"},
            "update_encounter_draft": {
                "encounter_id", "draft_revision", "patch"
            },
            "duplicate_encounter_draft": {"encounter_id"},
            "start_saved_encounter": {"encounter_id", "draft_revision"},
            "pause_encounter": set(),
            "resume_encounter": set(),
        }
        if self.type in encounter_allowed:
            if set(payload) - encounter_allowed[self.type]:
                raise ValueError("Bilinmeyen encounter payload alani.")
            if self.type in {"pause_encounter", "resume_encounter"} and payload:
                raise ValueError("Encounter lifecycle payload bos olmali.")
        if self.type == "create_encounter_draft":
            name = payload.get("name")
            description = payload.get("description", "")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
                raise ValueError("Encounter adi gecersiz.")
            if not isinstance(description, str) or len(description) > 2000:
                raise ValueError("Encounter aciklamasi gecersiz.")
        if self.type in {
            "update_encounter_draft", "duplicate_encounter_draft",
            "start_saved_encounter",
        }:
            encounter_id = payload.get("encounter_id")
            if (
                not isinstance(encounter_id, str)
                or not 8 <= len(encounter_id) <= 64
            ):
                raise ValueError("encounter_id gecersiz.")
        if self.type in {"update_encounter_draft", "start_saved_encounter"}:
            revision = payload.get("draft_revision")
            if (
                not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision < 1
            ):
                raise ValueError("Encounter draft_revision gecersiz.")
        if self.type == "update_encounter_draft":
            patch = payload.get("patch")
            if (
                not isinstance(patch, dict)
                or set(patch) - {"name", "description", "combatants"}
            ):
                raise ValueError("Encounter patch gecersiz.")
        advanced_encounter_allowed = {
            "add_environment_entry": {
                "name", "kind", "initiative", "tie_breaker"
            },
            "set_initiative_tiebreaker": {
                "combatant_id", "tie_breaker"
            },
            "adjust_combatant_hp": {"combatant_id", "delta"},
            "undo_encounter": set(),
        }
        if self.type in advanced_encounter_allowed:
            if set(payload) - advanced_encounter_allowed[self.type]:
                raise ValueError("Bilinmeyen advanced encounter alani.")
        if self.type == "add_environment_entry":
            if (
                not isinstance(payload.get("name"), str)
                or not 1 <= len(payload["name"].strip()) <= 80
                or payload.get("kind") not in {"lair", "environment"}
            ):
                raise ValueError("Environment turn entry gecersiz.")
        if self.type in {
            "add_environment_entry", "set_initiative_tiebreaker"
        }:
            if self.type == "add_environment_entry":
                initiative = payload.get("initiative")
                if (
                    not isinstance(initiative, int)
                    or isinstance(initiative, bool)
                    or not -100 <= initiative <= 100
                ):
                    raise ValueError("Initiative gecersiz.")
            tie_breaker = payload.get("tie_breaker", 0)
            if (
                not isinstance(tie_breaker, int)
                or isinstance(tie_breaker, bool)
                or not -100 <= tie_breaker <= 100
            ):
                raise ValueError("Initiative tie breaker gecersiz.")
        if self.type in {
            "set_initiative_tiebreaker", "adjust_combatant_hp"
        }:
            combatant_id = payload.get("combatant_id")
            if (
                not isinstance(combatant_id, str)
                or not 1 <= len(combatant_id) <= 64
            ):
                raise ValueError("combatant_id gecersiz.")
        if self.type == "adjust_combatant_hp":
            delta = payload.get("delta")
            if (
                not isinstance(delta, int)
                or isinstance(delta, bool)
                or delta == 0
                or not -1_000_000 <= delta <= 1_000_000
            ):
                raise ValueError("Combatant HP delta gecersiz.")
        if self.type == "undo_encounter" and payload:
            raise ValueError("Undo payload bos olmali.")

        resource_allowed = {
            "short_rest": {"character_id", "hit_dice"},
            "long_rest": {"character_id"},
            "expend_resource": {"character_id", "resource_id", "amount"},
            "use_second_wind": {"character_id"},
            "death_save": {"character_id"},
            "start_concentration": {"character_id", "effect_id", "name"},
            "end_concentration": {"character_id"},
        }
        if self.type in resource_allowed:
            if set(payload) - resource_allowed[self.type]:
                raise ValueError("Bilinmeyen resource payload alani.")
            character_id = payload.get("character_id")
            if character_id is not None and (
                not isinstance(character_id, str)
                or not 1 <= len(character_id) <= 64
            ):
                raise ValueError("character_id gecersiz.")

        if self.type == "add_combatant":
            if set(payload) - {
                "id", "character_id", "name", "initiative", "tie_breaker",
                "hp", "kind", "hidden",
            }:
                raise ValueError("Bilinmeyen combatant alani.")
            name = payload.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ValueError("Katilimci adi 1 ile 80 karakter arasinda olmalidir.")
            if payload.get("kind", "monster") not in {"monster", "player", "npc"}:
                raise ValueError("Gecersiz katilimci turu.")
            for field in ("initiative", "tie_breaker"):
                value = payload.get(field, 0)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not -100 <= value <= 100
                ):
                    raise ValueError(f"Combatant {field} gecersiz.")
            hp = payload.get("hp")
            if hp is not None and (
                not isinstance(hp, int)
                or isinstance(hp, bool)
                or not 0 <= hp <= 1_000_000
            ):
                raise ValueError("Combatant HP gecersiz.")
            if not isinstance(payload.get("hidden", False), bool):
                raise ValueError("Combatant hidden boolean olmali.")

        if self.type == "update_character":
            allowed = {
                "character_id",
                "name",
                "level",
                "class_id",
                "species_id",
                "background_id",
                "inputs",
            }
            unknown = set(payload) - allowed
            if unknown:
                raise ValueError(
                    "Hesaplanan veya bilinmeyen karakter alanlari guncellenemez."
                )
            if "character_id" in payload and (
                not isinstance(payload["character_id"], str)
                or not 1 <= len(payload["character_id"]) <= 64
            ):
                raise ValueError("Gecerli character_id gereklidir.")
            if "name" in payload and (
                not isinstance(payload["name"], str)
                or not 1 <= len(payload["name"].strip()) <= 80
            ):
                raise ValueError("Karakter adi 1..80 karakter olmali.")
            if "level" in payload and (
                not isinstance(payload["level"], int)
                or isinstance(payload["level"], bool)
                or not 1 <= payload["level"] <= 20
            ):
                raise ValueError("Karakter seviyesi 1..20 arasinda olmali.")
            for field in ("class_id", "species_id", "background_id"):
                value = payload.get(field)
                if field in payload and value is not None and (
                    not isinstance(value, str) or not 1 <= len(value) <= 80
                ):
                    raise ValueError(f"{field} gecersiz.")
            if "inputs" in payload and not isinstance(payload["inputs"], dict):
                raise ValueError("inputs bir obje olmali.")

        if self.type == "short_rest":
            hit_dice = payload.get("hit_dice", 0)
            if (
                not isinstance(hit_dice, int)
                or isinstance(hit_dice, bool)
                or not 0 <= hit_dice <= 20
            ):
                raise ValueError("hit_dice 0..20 arasinda olmali.")

        if self.type == "expend_resource":
            resource_id = payload.get("resource_id")
            amount = payload.get("amount", 1)
            if not isinstance(resource_id, str) or not 1 <= len(resource_id) <= 80:
                raise ValueError("resource_id gecersiz.")
            if (
                not isinstance(amount, int)
                or isinstance(amount, bool)
                or not 1 <= amount <= 20
            ):
                raise ValueError("Resource amount 1..20 arasinda olmali.")

        if self.type == "start_concentration":
            for field in ("effect_id", "name"):
                value = payload.get(field)
                if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120:
                    raise ValueError(f"{field} gecersiz.")

        if self.type in {"add_condition", "remove_condition"}:
            for field in ("character_id", "condition_id"):
                value = payload.get(field)
                if not isinstance(value, str) or not 1 <= len(value) <= 80:
                    raise ValueError(f"{field} gecersiz.")
            if self.type == "add_condition" and not isinstance(
                payload.get("duration", {"kind": "permanent"}), dict
            ):
                raise ValueError("duration bir obje olmali.")

        return self


class RuleQuestionRequest(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    mode: Literal["rules", "story"] = "rules"


class AIDMStepRequest(BaseModel):
    objective: Annotated[str, Field(min_length=1, max_length=1000)] = "Continue the encounter"
    auto_apply: bool = False


class AuthContext(BaseModel):
    game_id: str
    member_id: str
    role: Role
    character_id: str | None = None
    is_owner: bool = False
    auth_token_id: str | None = None
    auth_expires_at: str | None = None
