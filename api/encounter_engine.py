from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4


ENCOUNTER_SCHEMA_VERSION = 1
ENCOUNTER_FIELDS = {"schema_version", "name", "description", "combatants"}
COMBATANT_FIELDS = {
    "id", "source", "name", "kind", "initiative", "hp", "max_hp",
    "armor_class", "hidden",
}


class EncounterValidationError(ValueError):
    pass


class EncounterStorageError(RuntimeError):
    pass


class EncounterDraftConflict(EncounterValidationError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Encounter revision conflict: expected {expected}, actual {actual}."
        )


class EncounterEngine:
    @staticmethod
    def create(name: str, description: str = "") -> dict[str, Any]:
        draft = {
            "schema_version": ENCOUNTER_SCHEMA_VERSION,
            "name": name.strip(),
            "description": description,
            "combatants": [],
        }
        EncounterEngine.validate(draft)
        return draft

    @staticmethod
    def patch(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        EncounterEngine.validate(draft)
        if (
            not isinstance(patch, dict)
            or set(patch) - {"name", "description", "combatants"}
        ):
            raise EncounterValidationError("Bilinmeyen encounter draft alani.")
        result = deepcopy(draft)
        for key, value in patch.items():
            result[key] = deepcopy(value)
        EncounterEngine.validate(result)
        return result

    @staticmethod
    def validate(draft: object) -> None:
        if (
            not isinstance(draft, dict)
            or set(draft) != ENCOUNTER_FIELDS
            or draft.get("schema_version") != ENCOUNTER_SCHEMA_VERSION
            or isinstance(draft.get("schema_version"), bool)
        ):
            raise EncounterValidationError("Encounter draft schema gecersiz.")
        name, description = draft["name"], draft["description"]
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 120
            or not isinstance(description, str)
            or len(description) > 2000
        ):
            raise EncounterValidationError("Encounter metadata gecersiz.")
        combatants = draft["combatants"]
        if not isinstance(combatants, list) or len(combatants) > 200:
            raise EncounterValidationError("Encounter combatant limiti asildi.")
        ids: set[str] = set()
        character_sources: set[str] = set()
        for combatant in combatants:
            EncounterEngine._validate_combatant(combatant)
            if combatant["id"] in ids:
                raise EncounterValidationError("Combatant id tekrar edemez.")
            ids.add(combatant["id"])
            source = combatant["source"]
            if source["type"] == "character":
                if source["id"] in character_sources:
                    raise EncounterValidationError(
                        "Ayni character encounter'a iki kez eklenemez."
                    )
                character_sources.add(source["id"])

    @staticmethod
    def _validate_combatant(combatant: object) -> None:
        if not isinstance(combatant, dict) or set(combatant) != COMBATANT_FIELDS:
            raise EncounterValidationError("Combatant schema gecersiz.")
        if (
            not isinstance(combatant["id"], str)
            or not 8 <= len(combatant["id"]) <= 64
            or not isinstance(combatant["name"], str)
            or not 1 <= len(combatant["name"].strip()) <= 80
            or combatant["kind"] not in {"monster", "npc", "player"}
            or not isinstance(combatant["hidden"], bool)
        ):
            raise EncounterValidationError("Combatant metadata gecersiz.")
        source = combatant["source"]
        if (
            not isinstance(source, dict)
            or set(source) != {"type", "id"}
            or source["type"] not in {"manual", "character"}
            or (
                source["type"] == "manual"
                and source["id"] is not None
            )
            or (
                source["type"] == "character"
                and (
                    not isinstance(source["id"], str)
                    or not 1 <= len(source["id"]) <= 64
                )
            )
        ):
            raise EncounterValidationError("Combatant source gecersiz.")
        for field, minimum, maximum in (
            ("initiative", -100, 100),
            ("hp", 0, 1_000_000),
            ("max_hp", 1, 1_000_000),
            ("armor_class", 0, 100),
        ):
            value = combatant[field]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise EncounterValidationError(f"Combatant {field} gecersiz.")
        if combatant["hp"] > combatant["max_hp"]:
            raise EncounterValidationError("Combatant HP max HP'yi asamaz.")

    @staticmethod
    def manual_combatant(
        name: str,
        kind: str,
        initiative: int,
        hp: int,
        max_hp: int,
        armor_class: int,
        hidden: bool = False,
    ) -> dict[str, Any]:
        combatant = {
            "id": uuid4().hex,
            "source": {"type": "manual", "id": None},
            "name": name.strip(),
            "kind": kind,
            "initiative": initiative,
            "hp": hp,
            "max_hp": max_hp,
            "armor_class": armor_class,
            "hidden": hidden,
        }
        EncounterEngine._validate_combatant(combatant)
        return combatant

    @staticmethod
    def hydrate(draft: dict[str, Any], characters: dict[str, dict]) -> list[dict]:
        EncounterEngine.validate(draft)
        hydrated: list[dict] = []
        for entry in draft["combatants"]:
            result = deepcopy(entry)
            source = entry["source"]
            if source["type"] == "character":
                character = characters.get(source["id"])
                if character is None:
                    raise EncounterValidationError(
                        "Encounter character kaynagi bulunamadi."
                    )
                result.update(
                    id=character["id"],
                    name=character["name"],
                    kind="player",
                    initiative=int(character["derived"]["initiative"]),
                    hp=int(character["hp"]),
                    max_hp=int(character["max_hp"]),
                    armor_class=int(character["ac"]),
                    hidden=False,
                )
            hydrated.append(result)
        hydrated.sort(key=lambda item: item["initiative"], reverse=True)
        return hydrated
