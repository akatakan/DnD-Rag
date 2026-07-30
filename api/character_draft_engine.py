from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from api.action_engine import ActionEngine, ActionValidationError
from api.character_engine import (
    ABILITY_KEYS,
    SKILL_ABILITIES,
    CharacterEngine,
    CharacterValidationError,
)
from api.inventory_engine import InventoryEngine, InventoryValidationError


DRAFT_SCHEMA_VERSION = 1
DRAFT_STEPS = (
    "basics",
    "abilities",
    "class",
    "species",
    "background",
    "proficiencies",
    "equipment",
    "spells",
    "review",
)
DRAFT_FIELDS = {
    "name",
    "ability_scores",
    "class_id",
    "species_id",
    "background_id",
    "skill_proficiencies",
    "skill_expertise",
    "equipment_catalog_ids",
    "spellcasting",
    "attacks",
}


class CharacterDraftValidationError(ValueError):
    pass


class CharacterDraftStorageError(RuntimeError):
    pass


class CharacterDraftEngine:
    def __init__(self, character_engine: CharacterEngine):
        self.character_engine = character_engine
        self.catalog = character_engine.catalog
        self.action_engine = ActionEngine(self.catalog)
        self.inventory_engine = InventoryEngine(self.catalog)

    def from_character(self, character: dict[str, Any]) -> dict[str, Any]:
        action_state = character["action_state"]
        skill_proficiencies = set(
            character["inputs"]["skill_proficiencies"]
        )
        background_id = character.get("background_id")
        if background_id is not None:
            background = self.catalog.get_entry(
                character["ruleset_version"], background_id
            )["entry"]
            skill_proficiencies.update(
                value.casefold().replace(" ", "_")
                for value in background["data"].get("skill_proficiencies", [])
            )
        equipment_catalog_ids: list[str] = []
        for item in character["inventory_state"]["entries"].values():
            catalog_id = item["catalog_id"]
            if catalog_id is None:
                continue
            remaining = 50 - len(equipment_catalog_ids)
            if remaining <= 0:
                break
            equipment_catalog_ids.extend(
                [catalog_id] * min(int(item["quantity"]), remaining)
            )
        return {
            "schema_version": DRAFT_SCHEMA_VERSION,
            "name": character["name"],
            "ability_scores": deepcopy(character["inputs"]["ability_scores"]),
            "class_id": character.get("class_id"),
            "species_id": character.get("species_id"),
            "background_id": character.get("background_id"),
            "skill_proficiencies": sorted(skill_proficiencies),
            "skill_expertise": list(character["inputs"]["skill_expertise"]),
            "equipment_catalog_ids": equipment_catalog_ids,
            "spellcasting": {
                "ability": action_state["spellcasting"]["ability"],
                "known_spell_ids": list(
                    action_state["spellcasting"]["known_spell_ids"]
                ),
                "prepared_spell_ids": list(
                    action_state["spellcasting"]["prepared_spell_ids"]
                ),
                "slots": {
                    level: pool["maximum"]
                    for level, pool in action_state["spellcasting"]["slots"].items()
                },
            },
            "attacks": list(deepcopy(action_state["attacks"]).values()),
        }

    def patch(self, draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        self.validate_shape(draft)
        if not isinstance(patch, dict) or set(patch) - DRAFT_FIELDS:
            raise CharacterDraftValidationError("Bilinmeyen draft alani.")
        result = deepcopy(draft)
        for key, value in patch.items():
            result[key] = deepcopy(value)
        self.validate_shape(result)
        return result

    def validate_shape(self, draft: dict[str, Any]) -> None:
        if (
            not isinstance(draft, dict)
            or set(draft) != {"schema_version", *DRAFT_FIELDS}
            or draft.get("schema_version") != DRAFT_SCHEMA_VERSION
            or isinstance(draft.get("schema_version"), bool)
        ):
            raise CharacterDraftValidationError("Character draft schema gecersiz.")
        if not isinstance(draft["name"], str) or len(draft["name"]) > 80:
            raise CharacterDraftValidationError("Character adi gecersiz.")
        scores = draft["ability_scores"]
        if (
            not isinstance(scores, dict)
            or set(scores) != set(ABILITY_KEYS)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= 20
                for value in scores.values()
            )
        ):
            raise CharacterDraftValidationError("Ability scores gecersiz.")
        for field, prefix in (
            ("class_id", "class:"),
            ("species_id", "species:"),
            ("background_id", "background:"),
        ):
            value = draft[field]
            if value is not None and (
                not isinstance(value, str)
                or not value.startswith(prefix)
                or len(value) > 80
            ):
                raise CharacterDraftValidationError(f"{field} gecersiz.")
        for field in ("skill_proficiencies", "skill_expertise"):
            values = draft[field]
            if (
                not isinstance(values, list)
                or len(values) > len(SKILL_ABILITIES)
                or len(values) != len(set(values))
                or any(value not in SKILL_ABILITIES for value in values)
            ):
                raise CharacterDraftValidationError(f"{field} gecersiz.")
        equipment = draft["equipment_catalog_ids"]
        if (
            not isinstance(equipment, list)
            or len(equipment) > 50
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 80
                for value in equipment
            )
        ):
            raise CharacterDraftValidationError("Equipment listesi gecersiz.")
        casting = draft["spellcasting"]
        if not isinstance(casting, dict) or set(casting) != {
            "ability", "known_spell_ids", "prepared_spell_ids", "slots"
        }:
            raise CharacterDraftValidationError("Spellcasting draft gecersiz.")
        if casting["ability"] is not None and casting["ability"] not in ABILITY_KEYS:
            raise CharacterDraftValidationError("Spellcasting ability gecersiz.")
        for field in ("known_spell_ids", "prepared_spell_ids"):
            values = casting[field]
            if (
                not isinstance(values, list)
                or len(values) > 500
                or len(values) != len(set(values))
                or any(
                    not isinstance(value, str) or not 1 <= len(value) <= 80
                    for value in values
                )
            ):
                raise CharacterDraftValidationError(f"{field} gecersiz.")
        if not set(casting["prepared_spell_ids"]) <= set(
            casting["known_spell_ids"]
        ):
            raise CharacterDraftValidationError(
                "Prepared spells, known spells icinde olmali."
            )
        slots = casting["slots"]
        if (
            not isinstance(slots, dict)
            or len(slots) > 9
            or any(
                level not in {str(value) for value in range(1, 10)}
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or not 0 <= maximum <= 99
                for level, maximum in slots.items()
            )
        ):
            raise CharacterDraftValidationError("Spell slots gecersiz.")
        if not isinstance(draft["attacks"], list) or len(draft["attacks"]) > 100:
            raise CharacterDraftValidationError("Attacks liste olmali.")

    def validate_step(
        self, draft: dict[str, Any], step: str, ruleset_version: str
    ) -> None:
        self.validate_shape(draft)
        if step not in DRAFT_STEPS:
            raise CharacterDraftValidationError("Builder step gecersiz.")
        if step in {"basics", "review"} and not draft["name"].strip():
            raise CharacterDraftValidationError("Character adi zorunludur.")
        expected_types = {
            "class": ("class_id", "class"),
            "species": ("species_id", "species"),
            "background": ("background_id", "background"),
        }
        if step in expected_types or step == "review":
            checks = (
                expected_types.values()
                if step == "review"
                else (expected_types[step],)
            )
            for field, entity_type in checks:
                entry_id = draft[field]
                if entry_id is None:
                    raise CharacterDraftValidationError(f"{field} zorunludur.")
                try:
                    entry = self.catalog.get_entry(ruleset_version, entry_id)["entry"]
                except KeyError as error:
                    raise CharacterDraftValidationError(
                        f"{field} katalogda bulunamadi."
                    ) from error
                if entry["type"] != entity_type:
                    raise CharacterDraftValidationError(f"{field} turu gecersiz.")
        if step in {"proficiencies", "review"} and not set(
            draft["skill_expertise"]
        ) <= set(draft["skill_proficiencies"]):
            raise CharacterDraftValidationError(
                "Expertise yalniz proficient skill icin secilebilir."
            )
        if step in {"proficiencies", "review"} and draft["background_id"]:
            background = self.catalog.get_entry(
                ruleset_version, draft["background_id"]
            )["entry"]
            required = {
                value.casefold().replace(" ", "_")
                for value in background["data"].get("skill_proficiencies", [])
            }
            if not required <= set(draft["skill_proficiencies"]):
                raise CharacterDraftValidationError(
                    "Background skill proficiencies eksik."
                )
        if step in {"equipment", "review"}:
            for entry_id in draft["equipment_catalog_ids"]:
                try:
                    entry = self.catalog.get_entry(ruleset_version, entry_id)["entry"]
                except KeyError as error:
                    raise CharacterDraftValidationError(
                        "Equipment katalog kaydi bulunamadi."
                    ) from error
                if entry["type"] != "item":
                    raise CharacterDraftValidationError(
                        "Equipment listesinde item olmayan kayit var."
                    )
        if step in {"spells", "review"}:
            probe = self.character_engine.new_character(
                "draft-probe", "draft-owner", draft["name"] or "Draft",
                ruleset_version,
            )
            try:
                self.action_engine.configure(
                    probe,
                    ability=draft["spellcasting"]["ability"],
                    known_spell_ids=draft["spellcasting"]["known_spell_ids"],
                    prepared_spell_ids=draft["spellcasting"]["prepared_spell_ids"],
                    slots=draft["spellcasting"]["slots"],
                    attacks=draft["attacks"],
                )
            except (ActionValidationError, KeyError) as error:
                raise CharacterDraftValidationError(str(error)) from error
        if step == "review":
            self.build_character(
                "draft-probe", "draft-owner", ruleset_version, draft
            )

    def build_character(
        self,
        character_id: str,
        owner_id: str,
        ruleset_version: str,
        draft: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_shape(draft)
        character = self.character_engine.new_character(
            character_id, owner_id, draft["name"].strip(), ruleset_version
        )
        try:
            character = self.character_engine.update(
                character,
                {
                    "name": draft["name"].strip(),
                    "class_id": draft["class_id"],
                    "species_id": draft["species_id"],
                    "background_id": draft["background_id"],
                    "inputs": {
                        "ability_scores": draft["ability_scores"],
                        "skill_proficiencies": draft["skill_proficiencies"],
                        "skill_expertise": draft["skill_expertise"],
                    },
                },
            )
            for entry_id in draft["equipment_catalog_ids"]:
                character = self.inventory_engine.add_item(
                    character, item_id=uuid4().hex, catalog_id=entry_id
                )
            character = self.character_engine.recalculate(character)
            character = self.action_engine.configure(
                character,
                ability=draft["spellcasting"]["ability"],
                known_spell_ids=draft["spellcasting"]["known_spell_ids"],
                prepared_spell_ids=draft["spellcasting"]["prepared_spell_ids"],
                slots=draft["spellcasting"]["slots"],
                attacks=draft["attacks"],
            )
            character["hp"] = character["max_hp"]
            return character
        except (
            CharacterValidationError,
            InventoryValidationError,
            ActionValidationError,
            KeyError,
        ) as error:
            raise CharacterDraftValidationError(str(error)) from error
