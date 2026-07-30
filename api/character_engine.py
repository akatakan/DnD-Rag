from __future__ import annotations

from copy import deepcopy
from typing import Any

from api.rules_catalog import RulesCatalog


ABILITY_KEYS = (
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
)
ABILITY_LABELS = {ability.title(): ability for ability in ABILITY_KEYS}
SKILL_ABILITIES = {
    "acrobatics": "dexterity",
    "animal_handling": "wisdom",
    "arcana": "intelligence",
    "athletics": "strength",
    "deception": "charisma",
    "history": "intelligence",
    "insight": "wisdom",
    "intimidation": "charisma",
    "investigation": "intelligence",
    "medicine": "wisdom",
    "nature": "intelligence",
    "perception": "wisdom",
    "performance": "charisma",
    "persuasion": "charisma",
    "religion": "intelligence",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "survival": "wisdom",
}
CHARACTER_SCHEMA_VERSION = 2


class CharacterValidationError(ValueError):
    pass


class CharacterEngine:
    def __init__(self, catalog: RulesCatalog | None = None):
        self.catalog = catalog or RulesCatalog()

    def new_character(
        self,
        character_id: str,
        owner_id: str,
        name: str,
        ruleset_version: str = "srd-5.2.1",
    ) -> dict[str, Any]:
        character = {
            "schema_version": CHARACTER_SCHEMA_VERSION,
            "id": character_id,
            "owner_id": owner_id,
            "ruleset_version": ruleset_version,
            "name": name,
            "level": 1,
            "class_id": "class:fighter",
            "legacy_class_name": None,
            "species_id": "species:human",
            "background_id": "background:acolyte",
            "inputs": {
                "ability_scores": {ability: 10 for ability in ABILITY_KEYS},
                "skill_proficiencies": [],
                "skill_expertise": [],
                "armor_class": {
                    "base": 10,
                    "add_dexterity": True,
                    "dexterity_cap": None,
                    "bonus": 0,
                },
                "hit_points": {
                    "level_one_base": None,
                    "per_level_base": 6,
                    "constitution_per_level": True,
                    "bonus": 0,
                },
                "speed": {"base": None, "bonus": 0},
            },
            "derived": {},
            "hp": 10,
            "temp_hp": 0,
            "conditions": [],
            "inventory": [],
        }
        recalculated = self.recalculate(character)
        from api.resource_engine import ResourceEngine
        from api.inventory_engine import InventoryEngine
        from api.action_engine import ActionEngine

        with_resources = ResourceEngine(self.catalog).initialize(recalculated)
        with_inventory = InventoryEngine(self.catalog).initialize(with_resources)
        with_actions = ActionEngine(self.catalog).initialize(with_inventory)
        return self.recalculate(with_actions)

    def migrate_legacy(
        self, character: dict[str, Any], ruleset_version: str = "srd-5.2.1"
    ) -> dict[str, Any]:
        if character.get("schema_version") == CHARACTER_SCHEMA_VERSION:
            return self.recalculate(character)

        legacy = deepcopy(character)
        class_name = str(legacy.get("class_name", "")).strip()
        class_id = "class:fighter" if class_name.casefold() == "fighter" else None
        level = self._integer(legacy.get("level", 1), "level", 1, 20)
        max_hp = self._integer(legacy.get("max_hp", 10), "max_hp", 1, 100_000)
        armor_class = self._integer(legacy.get("ac", 10), "ac", 0, 100)
        migrated = {
            "schema_version": CHARACTER_SCHEMA_VERSION,
            "id": str(legacy["id"]),
            "owner_id": str(legacy["owner_id"]),
            "ruleset_version": ruleset_version,
            "name": str(legacy.get("name", "Adventurer"))[:80],
            "level": level,
            "class_id": class_id,
            "legacy_class_name": class_name if class_id is None else None,
            "species_id": None,
            "background_id": None,
            "inputs": {
                "ability_scores": {ability: 10 for ability in ABILITY_KEYS},
                "skill_proficiencies": [],
                "skill_expertise": [],
                "armor_class": {
                    "base": armor_class,
                    "add_dexterity": False,
                    "dexterity_cap": None,
                    "bonus": 0,
                },
                "hit_points": {
                    "level_one_base": max_hp,
                    "per_level_base": 0,
                    "constitution_per_level": False,
                    "bonus": 0,
                },
                "speed": {"base": 30, "bonus": 0},
            },
            "derived": {},
            "hp": min(max_hp, max(0, int(legacy.get("hp", max_hp)))),
            "temp_hp": max(0, int(legacy.get("temp_hp", 0))),
            "conditions": list(legacy.get("conditions", [])),
            "inventory": list(legacy.get("inventory", [])),
        }
        return self.recalculate(migrated)

    def update(self, character: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "name",
            "level",
            "class_id",
            "species_id",
            "background_id",
            "inputs",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise CharacterValidationError(
                f"Hesaplanan veya bilinmeyen karakter alanlari degistirilemez: {', '.join(sorted(unknown))}"
            )
        updated = deepcopy(character)
        for key in allowed - {"inputs"}:
            if key in patch:
                updated[key] = patch[key]
        if "inputs" in patch:
            if not isinstance(patch["inputs"], dict):
                raise CharacterValidationError("inputs bir obje olmali.")
            input_patch = patch["inputs"]
            unknown_inputs = set(input_patch) - set(updated["inputs"])
            if unknown_inputs:
                raise CharacterValidationError(
                    f"Bilinmeyen character input alani: {', '.join(sorted(unknown_inputs))}"
                )
            for key, value in input_patch.items():
                if isinstance(updated["inputs"].get(key), dict):
                    if not isinstance(value, dict):
                        raise CharacterValidationError(f"{key} bir obje olmali.")
                    unknown_nested = set(value) - set(updated["inputs"][key])
                    if unknown_nested:
                        raise CharacterValidationError(
                            f"Bilinmeyen {key} alani: {', '.join(sorted(unknown_nested))}"
                        )
                    updated["inputs"][key].update(value)
                else:
                    updated["inputs"][key] = value
        recalculated = self.recalculate(updated)
        if "resource_state" in recalculated:
            from api.resource_engine import ResourceEngine

            recalculated = ResourceEngine(self.catalog).sync(recalculated)
        return recalculated

    def recalculate(self, character: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(character)
        self._validate_shape(result)
        level = self._integer(result["level"], "level", 1, 20)
        scores = result["inputs"]["ability_scores"]
        modifiers = {
            ability: (self._integer(scores[ability], ability, 1, 30) - 10) // 2
            for ability in ABILITY_KEYS
        }
        proficiency_bonus = 2 + (level - 1) // 4

        class_entry = self._optional_catalog_entry(
            result["ruleset_version"], result.get("class_id"), "class"
        )
        species_entry = self._optional_catalog_entry(
            result["ruleset_version"], result.get("species_id"), "species"
        )
        self._optional_catalog_entry(
            result["ruleset_version"], result.get("background_id"), "background"
        )
        if "inventory_state" in result:
            from api.inventory_engine import InventoryEngine

            result = InventoryEngine(self.catalog).sync(result)

        save_proficiencies = {
            ABILITY_LABELS[label]
            for label in (class_entry or {}).get("data", {}).get(
                "saving_throw_proficiencies", []
            )
            if label in ABILITY_LABELS
        }
        skill_proficiencies = self._skill_set(
            result["inputs"]["skill_proficiencies"], "skill_proficiencies"
        )
        skill_expertise = self._skill_set(
            result["inputs"]["skill_expertise"], "skill_expertise"
        )
        if not skill_expertise <= skill_proficiencies:
            raise CharacterValidationError(
                "Expertise yalnizca proficiency sahibi olunan skill'lerde secilebilir."
            )

        saves = {
            ability: modifiers[ability]
            + (proficiency_bonus if ability in save_proficiencies else 0)
            for ability in ABILITY_KEYS
        }
        skills = {
            skill: modifiers[ability]
            + proficiency_bonus
            * (2 if skill in skill_expertise else 1 if skill in skill_proficiencies else 0)
            for skill, ability in SKILL_ABILITIES.items()
        }

        armor = result["inputs"]["armor_class"]
        armor_base = self._integer(armor["base"], "armor_class.base", 0, 40)
        armor_bonus = self._integer(armor["bonus"], "armor_class.bonus", -10, 20)
        add_dexterity = self._boolean(
            armor["add_dexterity"], "armor_class.add_dexterity"
        )
        dexterity_cap = armor["dexterity_cap"]
        if dexterity_cap is not None:
            dexterity_cap = self._integer(
                dexterity_cap, "armor_class.dexterity_cap", 0, 10
            )
        dexterity_contribution = modifiers["dexterity"] if add_dexterity else 0
        if add_dexterity and dexterity_cap is not None:
            dexterity_contribution = min(dexterity_contribution, dexterity_cap)
        equipment_armor_bonus = result.get("inventory_state", {}).get(
            "derived", {}
        ).get("armor_class_bonus", 0)
        armor_class = max(
            0,
            armor_base
            + dexterity_contribution
            + armor_bonus
            + equipment_armor_bonus,
        )

        hp_input = result["inputs"]["hit_points"]
        level_one_base = hp_input["level_one_base"]
        if level_one_base is None:
            if class_entry is None:
                raise CharacterValidationError(
                    "Sinif katalog kaydi yoksa level_one_base zorunludur."
                )
            level_one_base = class_entry["data"]["hit_die"]
        level_one_base = self._integer(
            level_one_base, "hit_points.level_one_base", 1, 1_000_000
        )
        per_level_base = self._integer(
            hp_input["per_level_base"],
            "hit_points.per_level_base",
            0,
            1_000_000,
        )
        hp_bonus = self._integer(
            hp_input["bonus"], "hit_points.bonus", -1_000_000, 1_000_000
        )
        constitution_per_level = self._boolean(
            hp_input["constitution_per_level"],
            "hit_points.constitution_per_level",
        )
        constitution_modifier = (
            modifiers["constitution"] if constitution_per_level else 0
        )
        level_one_hp = max(1, level_one_base + constitution_modifier)
        later_level_hp = (
            max(1, per_level_base + constitution_modifier)
            if constitution_per_level
            else per_level_base
        )
        maximum_hp = level_one_hp + (level - 1) * later_level_hp + hp_bonus
        maximum_hp = max(1, maximum_hp)

        speed_input = result["inputs"]["speed"]
        speed_base = speed_input["base"]
        if speed_base is None:
            if species_entry is None:
                raise CharacterValidationError(
                    "Species katalog kaydi yoksa speed.base zorunludur."
                )
            speed_base = species_entry["data"]["speed"]
        speed_base = self._integer(speed_base, "speed.base", 0, 500)
        speed_bonus = self._integer(speed_input["bonus"], "speed.bonus", -500, 500)

        result["level"] = level
        result["derived"] = {
            "calculation_version": 1,
            "ability_modifiers": modifiers,
            "proficiency_bonus": proficiency_bonus,
            "saving_throws": saves,
            "skills": skills,
            "armor_class": armor_class,
            "initiative": modifiers["dexterity"],
            "max_hp": maximum_hp,
            "speed": max(0, speed_base + speed_bonus),
            "passive_perception": 10 + skills["perception"],
        }
        result["hp"] = min(maximum_hp, max(0, int(result.get("hp", maximum_hp))))
        result["temp_hp"] = max(0, int(result.get("temp_hp", 0)))
        # Compatibility projections are read-only and always regenerated.
        result["class_name"] = (
            class_entry["name"]
            if class_entry
            else str(result.get("legacy_class_name") or "Custom")
        )
        result["ac"] = armor_class
        result["max_hp"] = maximum_hp
        return result

    def _validate_shape(self, character: dict[str, Any]) -> None:
        if character.get("schema_version") != CHARACTER_SCHEMA_VERSION:
            raise CharacterValidationError("Desteklenmeyen character schema surumu.")
        if not isinstance(character.get("name"), str) or not 1 <= len(
            character["name"].strip()
        ) <= 80:
            raise CharacterValidationError("Karakter adi 1..80 karakter olmali.")
        if not isinstance(character.get("ruleset_version"), str):
            raise CharacterValidationError("Karakter ruleset surumu zorunludur.")
        inputs = character.get("inputs")
        if not isinstance(inputs, dict) or set(inputs) != {
            "ability_scores",
            "skill_proficiencies",
            "skill_expertise",
            "armor_class",
            "hit_points",
            "speed",
        }:
            raise CharacterValidationError("Character inputs schema ile eslesmiyor.")
        if not isinstance(inputs["ability_scores"], dict) or set(
            inputs["ability_scores"]
        ) != set(ABILITY_KEYS):
            raise CharacterValidationError("Alti ability score eksiksiz olmali.")
        nested_keys = {
            "armor_class": {"base", "add_dexterity", "dexterity_cap", "bonus"},
            "hit_points": {
                "level_one_base",
                "per_level_base",
                "constitution_per_level",
                "bonus",
            },
            "speed": {"base", "bonus"},
        }
        for key, expected in nested_keys.items():
            if not isinstance(inputs[key], dict) or set(inputs[key]) != expected:
                raise CharacterValidationError(f"{key} schema ile eslesmiyor.")

    def _optional_catalog_entry(
        self, version: str, entry_id: Any, expected_type: str
    ) -> dict[str, Any] | None:
        if entry_id is None:
            return None
        if not isinstance(entry_id, str):
            raise CharacterValidationError(f"{expected_type}_id metin olmali.")
        try:
            entry = self.catalog.get_entry(version, entry_id)["entry"]
        except (KeyError, ValueError) as error:
            raise CharacterValidationError(
                f"Katalog kaydi bulunamadi: {entry_id}"
            ) from error
        if entry["type"] != expected_type:
            raise CharacterValidationError(
                f"{entry_id} bir {expected_type} kaydi degil."
            )
        return entry

    @staticmethod
    def _skill_set(value: Any, field: str) -> set[str]:
        if (
            not isinstance(value, list)
            or len(value) > len(SKILL_ABILITIES)
            or any(not isinstance(item, str) or item not in SKILL_ABILITIES for item in value)
            or len(value) != len(set(value))
        ):
            raise CharacterValidationError(f"{field} gecersiz.")
        return set(value)

    @staticmethod
    def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise CharacterValidationError(
                f"{field} {minimum}..{maximum} araliginda tam sayi olmali."
            )
        return value

    @staticmethod
    def _boolean(value: Any, field: str) -> bool:
        if not isinstance(value, bool):
            raise CharacterValidationError(f"{field} boolean olmali.")
        return value
