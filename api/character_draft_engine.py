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


DRAFT_SCHEMA_VERSION = 2
LEGACY_DRAFT_SCHEMA_VERSION = 1
STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)
POINT_COSTS = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
ABILITY_SCORE_METHODS = {"standard_array", "point_cost", "legacy_manual"}
# The foundation SRD catalog deliberately keeps class records compact. Keep this
# compatibility policy pinned to the stable catalog ID until a future catalog
# version carries structured class skill-choice metadata.
CLASS_SKILL_POLICIES = {
    ("srd-5.2.1", "class:fighter"): {
        "count": 2,
        "options": frozenset(
            {
                "acrobatics",
                "animal_handling",
                "athletics",
                "history",
                "insight",
                "intimidation",
                "perception",
                "persuasion",
                "survival",
            }
        ),
    }
}
DRAFT_STEPS = (
    "basics",
    "class",
    "background",
    "species",
    "abilities",
    "proficiencies",
    "equipment",
    "spells",
    "review",
)
DRAFT_FIELDS = {
    "name",
    "ability_scores",
    "ability_score_method",
    "background_ability_increases",
    "class_id",
    "species_id",
    "background_id",
    "skill_proficiencies",
    "skill_expertise",
    "equipment_catalog_ids",
    "spellcasting",
    "attacks",
}
LEGACY_DRAFT_FIELDS = DRAFT_FIELDS - {
    "ability_score_method",
    "background_ability_increases",
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
            "ability_score_method": "legacy_manual",
            "background_ability_increases": {},
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

    def new_creation_draft(self, character: dict[str, Any]) -> dict[str, Any]:
        draft = self.from_character(character)
        draft["ability_score_method"] = "standard_array"
        draft["ability_scores"] = {
            "strength": 15,
            "dexterity": 14,
            "constitution": 13,
            "intelligence": 8,
            "wisdom": 10,
            "charisma": 12,
        }
        draft["background_ability_increases"] = {
            "intelligence": 2,
            "wisdom": 1,
        }
        draft["spellcasting"] = self.initial_spellcasting(
            character["ruleset_version"], draft["class_id"]
        )
        self.validate_shape(draft)
        return draft

    def spellcasting_policy(
        self,
        ruleset_version: str,
        class_id: str | None,
        *,
        level: int = 1,
    ) -> dict[str, Any] | None:
        if class_id is None:
            return None
        try:
            class_entry = self.catalog.get_entry(
                ruleset_version, class_id
            )["entry"]
        except KeyError:
            return None
        raw = class_entry["data"].get("spellcasting")
        if not isinstance(raw, dict):
            return None
        level_key = str(level)
        known_count = int(raw["known_count_by_level"].get(level_key, 0))
        prepared_count = int(
            raw["prepared_count_by_level"].get(level_key, 0)
        )
        slots = deepcopy(raw["slots_by_level"].get(level_key, {}))
        if known_count == 0 and prepared_count == 0 and not slots:
            return None
        return {
            "ability": raw["ability"].casefold(),
            "spell_ids": frozenset(raw["spell_ids"]),
            "known_count": known_count,
            "prepared_count": prepared_count,
            "slots": slots,
        }

    def initial_spellcasting(
        self, ruleset_version: str, class_id: str | None
    ) -> dict[str, Any]:
        policy = self.spellcasting_policy(ruleset_version, class_id)
        if policy is None:
            return {
                "ability": None,
                "known_spell_ids": [],
                "prepared_spell_ids": [],
                "slots": {},
            }
        return {
            "ability": policy["ability"],
            "known_spell_ids": [],
            "prepared_spell_ids": [],
            "slots": deepcopy(policy["slots"]),
        }

    def reconcile_spellcasting(
        self, draft: dict[str, Any], ruleset_version: str
    ) -> dict[str, Any]:
        """Normalize an active pre-policy draft without trusting old slot input."""
        self.validate_shape(draft)
        result = deepcopy(draft)
        policy = self.spellcasting_policy(
            ruleset_version, result["class_id"]
        )
        if policy is None:
            result["spellcasting"] = self.initial_spellcasting(
                ruleset_version, result["class_id"]
            )
            return result
        old = result["spellcasting"]
        known = [
            spell_id
            for spell_id in old["known_spell_ids"]
            if spell_id in policy["spell_ids"]
        ][: policy["known_count"]]
        prepared = [
            spell_id
            for spell_id in old["prepared_spell_ids"]
            if spell_id in known
        ][: policy["prepared_count"]]
        result["spellcasting"] = {
            "ability": policy["ability"],
            "known_spell_ids": known,
            "prepared_spell_ids": prepared,
            "slots": deepcopy(policy["slots"]),
        }
        return result

    def quick_build(
        self,
        draft: dict[str, Any],
        ruleset_version: str,
        *,
        name: str,
        class_id: str,
        species_id: str,
    ) -> dict[str, Any]:
        """Create a deterministic, reviewable level-1 draft from three choices.

        Quick Build is intentionally server-authored: the client selects only the
        public product choices while catalog IDs, legal score allocation, origin
        bonuses, proficiencies, equipment, and spell policy are derived here.
        """
        self.validate_shape(draft)
        clean_name = name.strip()
        if not clean_name:
            raise CharacterDraftValidationError("Character adi zorunludur.")
        try:
            class_entry = self.catalog.get_entry(
                ruleset_version, class_id
            )["entry"]
            species_entry = self.catalog.get_entry(
                ruleset_version, species_id
            )["entry"]
        except KeyError as error:
            raise CharacterDraftValidationError(
                "Quick Build secimi katalogda bulunamadi."
            ) from error
        if class_entry["type"] != "class" or species_entry["type"] != "species":
            raise CharacterDraftValidationError("Quick Build secim turu gecersiz.")

        backgrounds = self.catalog.list_entries(
            ruleset_version, entity_type="background", limit=100
        )["entries"]
        if not backgrounds:
            raise CharacterDraftValidationError(
                "Quick Build icin yayinlanmis background bulunamadi."
            )
        background = self._recommended_background(class_entry, backgrounds)
        scores = self._recommended_standard_array(class_entry)
        increases = self._recommended_background_increases(
            class_entry, background, scores
        )
        skills = self._recommended_skills(
            ruleset_version, class_entry, species_entry, background
        )
        items = self.catalog.list_entries(
            ruleset_version, entity_type="item", limit=100
        )["entries"]

        result = deepcopy(draft)
        result.update(
            {
                "name": clean_name,
                "ability_score_method": "standard_array",
                "ability_scores": scores,
                "background_ability_increases": increases,
                "class_id": class_id,
                "species_id": species_id,
                "background_id": background["id"],
                "skill_proficiencies": skills,
                "skill_expertise": [],
                # The foundation catalog does not yet model structured starting
                # packages. Include only an unambiguous singleton item; never
                # invent quantities, currency, or a client-authored loadout.
                "equipment_catalog_ids": (
                    [items[0]["id"]] if len(items) == 1 else []
                ),
                "spellcasting": self.initial_spellcasting(
                    ruleset_version, class_id
                ),
                "attacks": [],
            }
        )
        self.validate_step(result, "review", ruleset_version)
        return result

    @staticmethod
    def _recommended_background(
        class_entry: dict[str, Any], backgrounds: list[dict[str, Any]]
    ) -> dict[str, Any]:
        primary = {
            value.casefold()
            for value in class_entry["data"].get("primary_abilities", [])
            if isinstance(value, str)
        }
        for background in backgrounds:
            options = {
                value.casefold()
                for value in background["data"].get("ability_options", [])
                if isinstance(value, str)
            }
            if primary & options:
                return background
        return backgrounds[0]

    @staticmethod
    def _recommended_standard_array(
        class_entry: dict[str, Any],
    ) -> dict[str, int]:
        suggestions = {
            "barbarian": (15, 13, 14, 10, 12, 8),
            "bard": (8, 14, 12, 13, 10, 15),
            "cleric": (14, 8, 13, 10, 15, 12),
            "druid": (8, 12, 14, 13, 15, 10),
            "fighter": (15, 14, 13, 8, 10, 12),
            "monk": (12, 15, 13, 10, 14, 8),
            "paladin": (15, 10, 13, 8, 12, 14),
            "ranger": (12, 15, 13, 8, 14, 10),
            "rogue": (12, 15, 13, 14, 10, 8),
            "sorcerer": (10, 13, 14, 8, 12, 15),
            "warlock": (8, 14, 13, 12, 10, 15),
            "wizard": (8, 12, 13, 15, 14, 10),
        }
        slug = class_entry["id"].split(":", 1)[-1]
        values = suggestions.get(slug, STANDARD_ARRAY)
        return dict(zip(ABILITY_KEYS, values, strict=True))

    @staticmethod
    def _recommended_background_increases(
        class_entry: dict[str, Any],
        background: dict[str, Any],
        scores: dict[str, int],
    ) -> dict[str, int]:
        options = [
            value.casefold()
            for value in background["data"].get("ability_options", [])
            if isinstance(value, str) and value.casefold() in ABILITY_KEYS
        ]
        if len(options) < 2:
            raise CharacterDraftValidationError(
                "Background ability secenekleri Quick Build icin yetersiz."
            )
        primary = [
            value.casefold()
            for value in class_entry["data"].get("primary_abilities", [])
            if isinstance(value, str) and value.casefold() in options
        ]
        ordered = primary + [
            ability
            for ability in sorted(options, key=lambda key: scores[key], reverse=True)
            if ability not in primary
        ]
        return {ordered[0]: 2, ordered[1]: 1}

    def _recommended_skills(
        self,
        ruleset_version: str,
        class_entry: dict[str, Any],
        species_entry: dict[str, Any],
        background: dict[str, Any],
    ) -> list[str]:
        selected = [
            value.casefold().replace(" ", "_")
            for value in background["data"].get("skill_proficiencies", [])
        ]
        policy = CLASS_SKILL_POLICIES.get(
            (ruleset_version, class_entry["id"])
        )
        if policy is None and class_entry["id"] == "class:fighter":
            policy = CLASS_SKILL_POLICIES[("srd-5.2.1", "class:fighter")]
        count = int(
            class_entry["data"].get(
                "skill_proficiency_count",
                policy["count"] if policy else 0,
            )
        )
        options = class_entry["data"].get(
            "skill_proficiency_options",
            sorted(policy["options"]) if policy else [],
        )
        for skill in options:
            normalized = skill.casefold().replace(" ", "_")
            if normalized not in selected and count:
                selected.append(normalized)
                count -= 1
        if count:
            raise CharacterDraftValidationError(
                "Class skill secenekleri Quick Build icin yetersiz."
            )
        traits = {
            value.casefold()
            for value in species_entry["data"].get("traits", [])
            if isinstance(value, str)
        }
        species_count = int(
            species_entry["data"].get(
                "skill_choice_count", 1 if "skillful" in traits else 0
            )
        )
        for skill in SKILL_ABILITIES:
            if skill not in selected and species_count:
                selected.append(skill)
                species_count -= 1
        if species_count:
            raise CharacterDraftValidationError(
                "Species skill secenekleri Quick Build icin yetersiz."
            )
        return sorted(selected)

    @staticmethod
    def migrate_v1(draft: dict[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(draft, dict)
            or draft.get("schema_version") != LEGACY_DRAFT_SCHEMA_VERSION
            or set(draft) != {"schema_version", *LEGACY_DRAFT_FIELDS}
        ):
            raise CharacterDraftValidationError(
                "Legacy character draft schema gecersiz."
            )
        migrated = deepcopy(draft)
        migrated["schema_version"] = DRAFT_SCHEMA_VERSION
        migrated["ability_score_method"] = "legacy_manual"
        migrated["background_ability_increases"] = {}
        return migrated

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
        if draft["ability_score_method"] not in ABILITY_SCORE_METHODS:
            raise CharacterDraftValidationError(
                "Ability score yontemi gecersiz."
            )
        increases = draft["background_ability_increases"]
        if (
            not isinstance(increases, dict)
            or set(increases) - set(ABILITY_KEYS)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value not in {1, 2}
                for value in increases.values()
            )
            or sum(increases.values()) > 3
        ):
            raise CharacterDraftValidationError(
                "Background ability artislari gecersiz."
            )
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
        if step in {"abilities", "review"}:
            self._validate_ability_scores(draft)
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
        if step in {"background", "review"}:
            self._validate_background_increases(draft, ruleset_version)
        if step in {"proficiencies", "review"}:
            self._validate_skill_proficiencies(draft, ruleset_version)
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
            self._validate_spellcasting_choices(draft, ruleset_version)
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

    def _validate_spellcasting_choices(
        self, draft: dict[str, Any], ruleset_version: str
    ) -> None:
        casting = draft["spellcasting"]
        policy = self.spellcasting_policy(
            ruleset_version, draft["class_id"]
        )
        if policy is None:
            if casting != {
                "ability": None,
                "known_spell_ids": [],
                "prepared_spell_ids": [],
                "slots": {},
            }:
                raise CharacterDraftValidationError(
                    "Secili class 1. seviyede spellcasting kullanamaz."
                )
            return
        if casting["ability"] != policy["ability"]:
            raise CharacterDraftValidationError(
                "Spellcasting ability class tarafindan belirlenir."
            )
        known = set(casting["known_spell_ids"])
        prepared = set(casting["prepared_spell_ids"])
        if not known <= policy["spell_ids"]:
            raise CharacterDraftValidationError(
                "Class spell listesinde olmayan bir spell secildi."
            )
        if len(known) > policy["known_count"]:
            raise CharacterDraftValidationError(
                f"En fazla {policy['known_count']} spell bilinebilir."
            )
        if len(prepared) > policy["prepared_count"]:
            raise CharacterDraftValidationError(
                f"En fazla {policy['prepared_count']} spell hazirlanabilir."
            )
        if casting["slots"] != policy["slots"]:
            raise CharacterDraftValidationError(
                "Spell slot maksimumlari class ve level tarafindan belirlenir."
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
        final_ability_scores = {
            ability: draft["ability_scores"][ability]
            + draft["background_ability_increases"].get(ability, 0)
            for ability in ABILITY_KEYS
        }
        try:
            class_entry = self.catalog.get_entry(
                ruleset_version, draft["class_id"]
            )["entry"]
            hit_die = int(class_entry["data"]["hit_die"])
            average_hp = int(
                class_entry["data"].get(
                    "average_hp_per_level", hit_die // 2 + 1
                )
            )
            character = self.character_engine.update(
                character,
                {
                    "name": draft["name"].strip(),
                    "class_id": draft["class_id"],
                    "species_id": draft["species_id"],
                    "background_id": draft["background_id"],
                    "inputs": {
                        "ability_scores": final_ability_scores,
                        "skill_proficiencies": draft["skill_proficiencies"],
                        "skill_expertise": draft["skill_expertise"],
                        "hit_points": {
                            "level_one_base": hit_die,
                            "per_level_base": average_hp,
                        },
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

    @staticmethod
    def _validate_ability_scores(draft: dict[str, Any]) -> None:
        method = draft["ability_score_method"]
        scores = list(draft["ability_scores"].values())
        if method == "legacy_manual":
            raise CharacterDraftValidationError(
                "Devam etmek icin Standard Array veya Point Cost secilmelidir."
            )
        if method == "standard_array":
            if sorted(scores, reverse=True) != list(STANDARD_ARRAY):
                raise CharacterDraftValidationError(
                    "Standard Array 15, 14, 13, 12, 10 ve 8 degerlerini "
                    "tam olarak birer kez kullanmalidir."
                )
            return
        if any(score not in POINT_COSTS for score in scores):
            raise CharacterDraftValidationError(
                "Point Cost ability score'lari 8 ile 15 arasinda olmalidir."
            )
        spent = sum(POINT_COSTS[score] for score in scores)
        if spent != 27:
            raise CharacterDraftValidationError(
                f"Point Cost tam 27 puan kullanmalidir; kullanilan {spent}."
            )

    def _validate_background_increases(
        self, draft: dict[str, Any], ruleset_version: str
    ) -> None:
        background_id = draft["background_id"]
        if background_id is None:
            raise CharacterDraftValidationError("background_id zorunludur.")
        try:
            background = self.catalog.get_entry(
                ruleset_version, background_id
            )["entry"]
        except KeyError as error:
            raise CharacterDraftValidationError(
                "Background katalogda bulunamadi."
            ) from error
        options = {
            value.casefold()
            for value in background["data"].get("ability_options", [])
        }
        increases = draft["background_ability_increases"]
        if not set(increases) <= options:
            raise CharacterDraftValidationError(
                "Background ability artislari katalog secenekleriyle uyusmuyor."
            )
        distribution = sorted(increases.values(), reverse=True)
        if distribution not in ([2, 1], [1, 1, 1]):
            raise CharacterDraftValidationError(
                "Background ability artislari +2/+1 veya +1/+1/+1 olmali."
            )
        if any(
            draft["ability_scores"][ability] + bonus > 20
            for ability, bonus in increases.items()
        ):
            raise CharacterDraftValidationError(
                "Background artisi ability score'u 20 uzerine cikaramaz."
            )

    def _validate_skill_proficiencies(
        self, draft: dict[str, Any], ruleset_version: str
    ) -> None:
        selected = set(draft["skill_proficiencies"])
        if not set(draft["skill_expertise"]) <= selected:
            raise CharacterDraftValidationError(
                "Expertise yalniz proficient skill icin secilebilir."
            )
        if not draft["background_id"]:
            raise CharacterDraftValidationError("background_id zorunludur.")
        if not draft["class_id"]:
            raise CharacterDraftValidationError("class_id zorunludur.")
        if not draft["species_id"]:
            raise CharacterDraftValidationError("species_id zorunludur.")

        try:
            background = self.catalog.get_entry(
                ruleset_version, draft["background_id"]
            )["entry"]
            species = self.catalog.get_entry(
                ruleset_version, draft["species_id"]
            )["entry"]
            class_entry = self.catalog.get_entry(
                ruleset_version, draft["class_id"]
            )["entry"]
        except KeyError as error:
            raise CharacterDraftValidationError(
                "Skill proficiency kaynagi katalogda bulunamadi."
            ) from error

        background_skills = {
            value.casefold().replace(" ", "_")
            for value in background["data"].get("skill_proficiencies", [])
        }
        if not background_skills <= selected:
            raise CharacterDraftValidationError(
                "Background skill proficiencies eksik."
            )

        class_data = class_entry["data"]
        policy = CLASS_SKILL_POLICIES.get(
            (ruleset_version, draft["class_id"])
        )
        if policy is None and draft["class_id"] == "class:fighter":
            policy = CLASS_SKILL_POLICIES[("srd-5.2.1", "class:fighter")]
        class_choice_count = class_data.get("skill_proficiency_count")
        class_options = class_data.get("skill_proficiency_options")
        if class_choice_count is None and policy is not None:
            class_choice_count = policy["count"]
        if class_options is None and policy is not None:
            class_options = policy["options"]
        if class_choice_count is None or class_options is None:
            raise CharacterDraftValidationError(
                "Class skill proficiency kurali desteklenmiyor."
            )
        normalized_class_options = {
            value.casefold().replace(" ", "_") for value in class_options
        }
        species_traits = {
            value.casefold()
            for value in species["data"].get("traits", [])
            if isinstance(value, str)
        }
        species_choice_count = int(
            species["data"].get(
                "skill_choice_count",
                1 if "skillful" in species_traits else 0,
            )
        )
        extra_skills = selected - background_skills
        expected_extra_count = int(class_choice_count) + species_choice_count
        if len(extra_skills) != expected_extra_count:
            raise CharacterDraftValidationError(
                "Skill proficiency sayisi gecersiz: background disinda "
                f"tam {expected_extra_count} secim yapilmalidir."
            )
        class_eligible = extra_skills & normalized_class_options
        if len(class_eligible) < int(class_choice_count):
            raise CharacterDraftValidationError(
                "Class icin izin verilen listeden gerekli skill "
                "proficiency secimleri ayrilabilmelidir."
            )
