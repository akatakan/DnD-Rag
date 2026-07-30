from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from api.character_engine import ABILITY_KEYS, SKILL_ABILITIES
from api.rules_catalog import RulesCatalog
from dice import DiceError, parse_roll, roll


ACTION_SCHEMA_VERSION = 1
ROLL_MODES = {"normal", "advantage", "disadvantage"}
MAX_ATTACKS = 100
MAX_SPELLS = 500


class ActionValidationError(ValueError):
    pass


class ActionEngine:
    """Typed, server-authoritative character actions and spell resources."""

    def __init__(self, catalog: RulesCatalog | None = None):
        self.catalog = catalog or RulesCatalog()

    def initialize(self, character: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(character)
        result["action_state"] = {
            "schema_version": ACTION_SCHEMA_VERSION,
            "spellcasting": {
                "ability": None,
                "known_spell_ids": [],
                "prepared_spell_ids": [],
                "slots": {},
            },
            "attacks": {},
        }
        return self.sync(result)

    def sync(self, character: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(character)
        self.validate(result)
        spellcasting = result["action_state"]["spellcasting"]
        prepared = set(spellcasting["prepared_spell_ids"])
        known = set(spellcasting["known_spell_ids"])
        if not prepared <= known:
            raise ActionValidationError("Prepared spells, known spells icinde olmali.")
        return result

    def validate(self, character: dict[str, Any]) -> None:
        state = character.get("action_state")
        if (
            not isinstance(state, dict)
            or set(state) != {"schema_version", "spellcasting", "attacks"}
        ):
            raise ActionValidationError("Character action state eksik veya gecersiz.")
        version = state.get("schema_version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != ACTION_SCHEMA_VERSION
        ):
            if (
                isinstance(version, int)
                and not isinstance(version, bool)
                and version > ACTION_SCHEMA_VERSION
            ):
                raise ActionValidationError(
                    "Character action schema uygulamadan daha yeni."
                )
            raise ActionValidationError("Character action schema version gecersiz.")
        spellcasting = state.get("spellcasting")
        attacks = state.get("attacks")
        if (
            not isinstance(spellcasting, dict)
            or set(spellcasting)
            != {
                "ability",
                "known_spell_ids",
                "prepared_spell_ids",
                "slots",
            }
            or not isinstance(attacks, dict)
        ):
            raise ActionValidationError("Action state bolumleri gecersiz.")
        ability = spellcasting.get("ability")
        if ability is not None and ability not in ABILITY_KEYS:
            raise ActionValidationError("Spellcasting ability gecersiz.")
        for field in ("known_spell_ids", "prepared_spell_ids"):
            values = spellcasting.get(field)
            if (
                not isinstance(values, list)
                or len(values) > MAX_SPELLS
                or any(
                    not isinstance(value, str) or not 1 <= len(value) <= 80
                    for value in values
                )
                or len(values) != len(set(values))
            ):
                raise ActionValidationError(f"{field} gecersiz.")
        slots = spellcasting.get("slots")
        if not isinstance(slots, dict):
            raise ActionValidationError("Spell slots gecersiz.")
        for level, pool in slots.items():
            if level not in {str(value) for value in range(1, 10)}:
                raise ActionValidationError("Spell slot level gecersiz.")
            if not isinstance(pool, dict) or set(pool) != {"maximum", "remaining"}:
                raise ActionValidationError("Spell slot pool gecersiz.")
            maximum, remaining = pool.get("maximum"), pool.get("remaining")
            if (
                not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or not isinstance(remaining, int)
                or isinstance(remaining, bool)
                or not 0 <= remaining <= maximum <= 99
            ):
                raise ActionValidationError("Spell slot miktari gecersiz.")
        if len(attacks) > MAX_ATTACKS:
            raise ActionValidationError("En fazla 100 attack tanimlanabilir.")
        for attack_id, attack in attacks.items():
            self._validate_attack(attack_id, attack)

    def configure(
        self,
        character: dict[str, Any],
        *,
        ability: str | None,
        known_spell_ids: list[str],
        prepared_spell_ids: list[str],
        slots: dict[str, int],
        attacks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        result = self.sync(character)
        if ability is not None and ability not in ABILITY_KEYS:
            raise ActionValidationError("Spellcasting ability gecersiz.")
        known = self._spell_ids(result, known_spell_ids)
        prepared = self._spell_ids(result, prepared_spell_ids)
        if not set(prepared) <= set(known):
            raise ActionValidationError("Prepared spells, known spells icinde olmali.")
        old_slots = result["action_state"]["spellcasting"]["slots"]
        if not isinstance(slots, dict) or len(slots) > 9:
            raise ActionValidationError("Spell slot yapilandirmasi gecersiz.")
        configured_slots: dict[str, dict[str, int]] = {}
        for raw_level, maximum in slots.items():
            level = str(raw_level)
            if (
                level not in {str(value) for value in range(1, 10)}
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or not 0 <= maximum <= 99
            ):
                raise ActionValidationError("Spell slot yapilandirmasi gecersiz.")
            old = old_slots.get(level, {"maximum": 0, "remaining": 0})
            spent = max(0, int(old["maximum"]) - int(old["remaining"]))
            configured_slots[level] = {
                "maximum": maximum,
                "remaining": max(0, maximum - spent),
            }
        if not isinstance(attacks, list) or len(attacks) > MAX_ATTACKS:
            raise ActionValidationError("En fazla 100 attack tanimlanabilir.")
        configured_attacks = {}
        for attack in attacks:
            if not isinstance(attack, dict):
                raise ActionValidationError("Attack kaydi gecersiz.")
            attack_id = str(attack.get("id", ""))
            self._validate_attack(attack_id, attack)
            if attack_id in configured_attacks:
                raise ActionValidationError("Attack kimligi tekrar edemez.")
            configured_attacks[attack_id] = deepcopy(attack)
        result["action_state"]["spellcasting"] = {
            "ability": ability,
            "known_spell_ids": known,
            "prepared_spell_ids": prepared,
            "slots": configured_slots,
        }
        result["action_state"]["attacks"] = configured_attacks
        return self.sync(result)

    def long_rest(self, character: dict[str, Any]) -> tuple[dict[str, Any], dict]:
        result = self.sync(character)
        recovered = {}
        for level, pool in result["action_state"]["spellcasting"]["slots"].items():
            amount = pool["maximum"] - pool["remaining"]
            pool["remaining"] = pool["maximum"]
            recovered[level] = amount
        return result, {"spell_slots_recovered": recovered}

    def roll_check(
        self, character: dict[str, Any], category: str, key: str, mode: str
    ) -> dict:
        result = self.sync(character)
        if category == "ability":
            if key not in ABILITY_KEYS:
                raise ActionValidationError("Ability check anahtari gecersiz.")
            modifier = result["derived"]["ability_modifiers"][key]
        elif category == "skill":
            if key not in SKILL_ABILITIES:
                raise ActionValidationError("Skill check anahtari gecersiz.")
            modifier = result["derived"]["skills"][key]
        elif category == "save":
            if key not in ABILITY_KEYS:
                raise ActionValidationError("Saving throw anahtari gecersiz.")
            modifier = result["derived"]["saving_throws"][key]
        else:
            raise ActionValidationError("Roll category gecersiz.")
        intent = self._intent(category, result, f"{category}:{key}", mode, modifier)
        return self._resolve(intent)

    def attack(
        self, character: dict[str, Any], attack_id: str, target: dict, mode: str
    ) -> dict:
        result = self.sync(character)
        attack = result["action_state"]["attacks"].get(attack_id)
        if attack is None:
            raise ActionValidationError("Attack bulunamadi.")
        modifier = result["derived"]["ability_modifiers"][attack["ability"]]
        if attack["proficient"]:
            modifier += result["derived"]["proficiency_bonus"]
        intent = self._intent("attack", result, attack_id, mode, modifier)
        intent["target"] = {
            "character_id": target["id"],
            "armor_class": target["derived"]["armor_class"],
        }
        intent["effect"] = {
            "kind": "damage",
            "damage_type": attack["damage_type"],
            "expression": attack["damage_dice"],
        }
        resolved = self._resolve(intent)
        resolved["hit"] = resolved["roll"]["total"] >= intent["target"]["armor_class"]
        resolved["critical"] = resolved["roll"]["kept"][0] == 20
        resolved["automatic_miss"] = resolved["roll"]["kept"][0] == 1
        if resolved["automatic_miss"]:
            resolved["hit"] = False
        if resolved["critical"]:
            resolved["hit"] = True
        damage_expression = attack["damage_dice"]
        if resolved["critical"]:
            count, sides, _, modifier = parse_roll(damage_expression)
            damage_expression = f"{count * 2}d{sides}{modifier:+d}"
        if resolved["hit"]:
            damage = self._roll_payload(roll(damage_expression))
            # Damage rolls cannot heal or manufacture temporary HP.
            damage["total"] = max(0, damage["total"])
            resolved["damage"] = damage
        else:
            resolved["damage"] = None
        return resolved

    def cast_spell(
        self,
        character: dict[str, Any],
        spell_id: str,
        slot_level: int,
        target: dict,
    ) -> tuple[dict[str, Any], dict]:
        result = self.sync(character)
        casting = result["action_state"]["spellcasting"]
        if spell_id not in casting["known_spell_ids"]:
            raise ActionValidationError("Spell bilinmiyor.")
        if spell_id not in casting["prepared_spell_ids"]:
            raise ActionValidationError("Spell hazirlanmamis.")
        if casting["ability"] is None:
            raise ActionValidationError("Spellcasting ability yapilandirilmamis.")
        entry = self.catalog.get_entry(result["ruleset_version"], spell_id)["entry"]
        spell_level = int(entry["data"]["level"])
        if spell_id != "spell:cure-wounds":
            raise ActionValidationError("Bu spell icin typed resolver henuz yok.")
        if not spell_level <= slot_level <= 9:
            raise ActionValidationError("Spell slot level gecersiz.")
        pool = casting["slots"].get(str(slot_level))
        if pool is None or pool["remaining"] < 1:
            raise ActionValidationError("Yeterli spell slot yok.")
        pool["remaining"] -= 1
        modifier = result["derived"]["ability_modifiers"][casting["ability"]]
        expression = f"{slot_level * 2}d8{modifier:+d}"
        intent = self._intent("spell", result, spell_id, "normal", modifier)
        intent["action_cost"] = "action"
        intent["target"] = {"character_id": target["id"]}
        intent["slot_level"] = slot_level
        intent["effect"] = {"kind": "healing", "expression": expression}
        healing = self._roll_payload(roll(expression))
        # A negative healing roll never becomes damage.
        healing["total"] = max(0, healing["total"])
        resolved = {
            "intent": intent,
            "healing": healing,
        }
        return result, resolved

    @staticmethod
    def feature_intent(character: dict, feature_id: str, expression: str) -> dict:
        return {
            "schema_version": 1,
            "intent_id": uuid4().hex,
            "kind": "feature",
            "actor_character_id": character["id"],
            "source_id": feature_id,
            "action_cost": "bonus_action",
            "mode": "normal",
            "roll": {"expression": expression},
        }

    @staticmethod
    def _intent(
        kind: str, character: dict, source_id: str, mode: str, modifier: int
    ) -> dict:
        if mode not in ROLL_MODES:
            raise ActionValidationError("Roll mode gecersiz.")
        expression = (
            f"1d20{modifier:+d}"
            if mode == "normal"
            else f"2d20{'kh1' if mode == 'advantage' else 'kl1'}{modifier:+d}"
        ).removesuffix("+0")
        return {
            "schema_version": 1,
            "intent_id": uuid4().hex,
            "kind": kind,
            "actor_character_id": character["id"],
            "source_id": source_id,
            "action_cost": "action" if kind in {"attack", "spell"} else None,
            "mode": mode,
            "roll": {"expression": expression, "modifier": modifier},
        }

    @classmethod
    def _resolve(cls, intent: dict) -> dict:
        return {"intent": intent, "roll": cls._roll_payload(roll(intent["roll"]["expression"]))}

    @staticmethod
    def _roll_payload(result) -> dict:
        return {
            "expression": result.expression,
            "rolls": list(result.rolls),
            "kept": list(result.kept),
            "modifier": result.modifier,
            "total": result.total,
        }

    def _spell_ids(self, character: dict, values: list[str]) -> list[str]:
        if (
            not isinstance(values, list)
            or len(values) > MAX_SPELLS
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 80
                for value in values
            )
            or len(values) != len(set(values))
        ):
            raise ActionValidationError("Spell listesi gecersiz.")
        for value in values:
            entry = self.catalog.get_entry(character["ruleset_version"], value)["entry"]
            if entry["type"] != "spell":
                raise ActionValidationError("Spell listesinde spell olmayan kayit var.")
        return list(values)

    @staticmethod
    def _validate_attack(attack_id: str, attack: Any) -> None:
        if (
            not isinstance(attack_id, str)
            or not isinstance(attack, dict)
            or not 1 <= len(attack_id) <= 80
        ):
            raise ActionValidationError("Attack kaydi gecersiz.")
        if set(attack) != {
            "id", "name", "ability", "proficient", "damage_dice", "damage_type"
        }:
            raise ActionValidationError("Attack alanlari gecersiz.")
        if attack["id"] != attack_id:
            raise ActionValidationError("Attack kimligi uyusmuyor.")
        if (
            not isinstance(attack["name"], str)
            or not 1 <= len(attack["name"].strip()) <= 120
            or attack["ability"] not in ABILITY_KEYS
            or not isinstance(attack["proficient"], bool)
            or not isinstance(attack["damage_type"], str)
            or not 1 <= len(attack["damage_type"]) <= 40
        ):
            raise ActionValidationError("Attack degerleri gecersiz.")
        try:
            count, _, keep, _ = parse_roll(attack["damage_dice"])
        except (DiceError, TypeError) as error:
            raise ActionValidationError("Attack damage dice gecersiz.") from error
        if keep is not None or count > 20:
            raise ActionValidationError("Attack damage dice gecersiz.")
