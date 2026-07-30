from __future__ import annotations

from copy import deepcopy
from typing import Any

from api.rules_catalog import RulesCatalog
from dice import roll


RESOURCE_SCHEMA_VERSION = 2


class ResourceValidationError(ValueError):
    pass


class ResourceEngine:
    def __init__(self, catalog: RulesCatalog | None = None):
        self.catalog = catalog or RulesCatalog()

    def initialize(self, character: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(character)
        level = int(result["level"])
        class_entry = self._class_entry(result)
        die_size = int((class_entry or {}).get("data", {}).get("hit_die", 8))
        class_resources: dict[str, dict[str, Any]] = {}
        if result.get("class_id") == "class:fighter":
            feature = self.catalog.get_entry(
                result["ruleset_version"], "feature:second-wind"
            )["entry"]
            maximum = self._second_wind_max(result, feature)
            class_resources["second-wind"] = {
                "source_id": feature["id"],
                "maximum": maximum,
                "remaining": maximum,
                "short_rest_recovery": 1,
                "long_rest_recovery": "all",
            }
        result["resource_state"] = {
            "schema_version": RESOURCE_SCHEMA_VERSION,
            "hit_dice": {
                "die_size": die_size,
                "maximum": level,
                "remaining": level,
            },
            "class_resources": class_resources,
            "death_saves": {
                "successes": 0,
                "failures": 0,
                "status": "none",
                "last_rolled_turn": None,
            },
        }
        result["effects"] = {
            "concentration": None,
            "conditions": self._migrate_conditions(result.get("conditions", [])),
        }
        return self.sync(result)

    def sync(self, character: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(character)
        self.validate(result)
        hit_dice = result["resource_state"]["hit_dice"]
        previous_maximum = hit_dice["maximum"]
        spent_hit_dice = max(0, previous_maximum - hit_dice["remaining"])
        class_entry = self._class_entry(result)
        hit_dice["die_size"] = int(
            (class_entry or {}).get("data", {}).get("hit_die", 8)
        )
        hit_dice["maximum"] = int(result["level"])
        hit_dice["remaining"] = max(0, hit_dice["maximum"] - spent_hit_dice)
        resources = result["resource_state"]["class_resources"]
        if result.get("class_id") == "class:fighter":
            feature = self.catalog.get_entry(
                result["ruleset_version"], "feature:second-wind"
            )["entry"]
            maximum = self._second_wind_max(result, feature)
            existing = resources.get("second-wind")
            spent = (
                max(0, existing["maximum"] - existing["remaining"])
                if existing is not None
                else 0
            )
            resources["second-wind"] = {
                "source_id": feature["id"],
                "maximum": maximum,
                "remaining": max(0, maximum - spent),
                "short_rest_recovery": 1,
                "long_rest_recovery": "all",
            }
        else:
            resources.pop("second-wind", None)
        result["conditions"] = [
            condition["name"] for condition in result["effects"]["conditions"]
        ]
        return result

    def short_rest(
        self, character: dict[str, Any], hit_dice_to_spend: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self.sync(character)
        if result["hp"] <= 0:
            raise ResourceValidationError("Short Rest icin en az 1 HP gerekir.")
        hit_dice = result["resource_state"]["hit_dice"]
        if (
            not isinstance(hit_dice_to_spend, int)
            or isinstance(hit_dice_to_spend, bool)
            or not 0 <= hit_dice_to_spend <= hit_dice["remaining"]
        ):
            raise ResourceValidationError("Harcanacak Hit Point Dice sayisi gecersiz.")
        constitution = result["derived"]["ability_modifiers"]["constitution"]
        rolls = []
        total_healing = 0
        for _ in range(hit_dice_to_spend):
            die = roll(f"1d{hit_dice['die_size']}").total
            healing = max(1, die + constitution)
            rolls.append({"roll": die, "modifier": constitution, "healing": healing})
            total_healing += healing
        before = result["hp"]
        result["hp"] = min(result["max_hp"], result["hp"] + total_healing)
        hit_dice["remaining"] -= hit_dice_to_spend
        recovered = {}
        for key, resource in result["resource_state"]["class_resources"].items():
            amount = min(
                resource["short_rest_recovery"],
                resource["maximum"] - resource["remaining"],
            )
            resource["remaining"] += amount
            recovered[key] = amount
        self._expire_rest_conditions(result, "short")
        return self.sync(result), {
            "hit_dice_spent": hit_dice_to_spend,
            "rolls": rolls,
            "healing": result["hp"] - before,
            "resources_recovered": recovered,
        }

    def long_rest(self, character: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self.sync(character)
        if result["hp"] <= 0:
            raise ResourceValidationError("Long Rest icin en az 1 HP gerekir.")
        before = result["hp"]
        result["hp"] = result["max_hp"]
        hit_dice = result["resource_state"]["hit_dice"]
        regained_hit_dice = hit_dice["maximum"] - hit_dice["remaining"]
        hit_dice["remaining"] = hit_dice["maximum"]
        recovered = {}
        for key, resource in result["resource_state"]["class_resources"].items():
            amount = resource["maximum"] - resource["remaining"]
            resource["remaining"] = resource["maximum"]
            recovered[key] = amount
        result["resource_state"]["death_saves"] = {
            "successes": 0,
            "failures": 0,
            "status": "none",
            "last_rolled_turn": None,
        }
        result["effects"]["concentration"] = None
        self._expire_rest_conditions(result, "long")
        return self.sync(result), {
            "healing": result["hp"] - before,
            "hit_dice_recovered": regained_hit_dice,
            "resources_recovered": recovered,
        }

    def expend_resource(
        self, character: dict[str, Any], resource_id: str, amount: int = 1
    ) -> dict[str, Any]:
        result = self.sync(character)
        resource = result["resource_state"]["class_resources"].get(resource_id)
        if resource is None:
            raise ResourceValidationError("Class resource bulunamadi.")
        if resource_id == "second-wind":
            raise ResourceValidationError(
                "Second Wind yalnizca use_second_wind komutuyla harcanabilir."
            )
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
            raise ResourceValidationError("Resource miktari pozitif tam sayi olmali.")
        if resource["remaining"] < amount:
            raise ResourceValidationError("Yeterli class resource yok.")
        resource["remaining"] -= amount
        return result

    def use_second_wind(
        self, character: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self.sync(character)
        if result["hp"] <= 0:
            raise ResourceValidationError("Second Wind icin en az 1 HP gerekir.")
        resource = result["resource_state"]["class_resources"].get("second-wind")
        if resource is None:
            raise ResourceValidationError("Second Wind ozelligi bulunamadi.")
        if resource["remaining"] < 1:
            raise ResourceValidationError("Second Wind kullanimi kalmadi.")
        resource["remaining"] -= 1
        value = roll("1d10").total
        healing = value + int(result["level"])
        before = result["hp"]
        result["hp"] = min(result["max_hp"], result["hp"] + healing)
        return result, {
            "roll": value,
            "modifier": int(result["level"]),
            "healing": result["hp"] - before,
            "remaining": resource["remaining"],
        }

    def death_save(
        self, character: dict[str, Any], turn_serial: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self.sync(character)
        saves = result["resource_state"]["death_saves"]
        if result["hp"] != 0 or saves["status"] in {"stable", "dead"}:
            raise ResourceValidationError("Death Saving Throw su anda yapilamaz.")
        if saves["last_rolled_turn"] == turn_serial:
            raise ResourceValidationError("Bu tur icin Death Saving Throw zaten yapildi.")
        saves["last_rolled_turn"] = turn_serial
        value = roll("1d20").total
        outcome = "success"
        if value == 20:
            result["hp"] = 1
            self._reset_death_saves(result)
            outcome = "revived"
        elif value == 1:
            saves["failures"] = min(3, saves["failures"] + 2)
            outcome = "double_failure"
        elif value >= 10:
            saves["successes"] += 1
        else:
            saves["failures"] += 1
            outcome = "failure"
        if result["hp"] == 0 and saves["failures"] >= 3:
            saves["status"] = "dead"
            result["effects"]["concentration"] = None
            outcome = "dead"
        elif result["hp"] == 0 and saves["successes"] >= 3:
            saves.update(successes=0, failures=0, status="stable")
            outcome = "stable"
        elif result["hp"] == 0:
            saves["status"] = "active"
        return self.sync(result), {"roll": value, "outcome": outcome}

    def apply_damage_state(
        self,
        character: dict[str, Any],
        damage: int,
        critical: bool = False,
        was_at_zero: bool = False,
        instant_death: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self.sync(character)
        saves = result["resource_state"]["death_saves"]
        if saves["status"] == "dead":
            result["effects"]["concentration"] = None
            return result, {
                "death_failures": 0,
                "death_status": "dead",
                "concentration_check": None,
            }
        death_failures = 0
        if instant_death:
            saves["status"] = "dead"
            result["effects"]["concentration"] = None
        elif was_at_zero and damage > 0:
            if damage >= result["max_hp"]:
                saves["status"] = "dead"
                result["effects"]["concentration"] = None
            else:
                death_failures = 2 if critical else 1
                saves["failures"] = min(3, saves["failures"] + death_failures)
                saves["status"] = "dead" if saves["failures"] >= 3 else "active"
        elif result["hp"] == 0 and saves["status"] == "none":
            saves["status"] = "active"
        if saves["status"] == "dead":
            result["effects"]["concentration"] = None
        concentration = None
        if result["hp"] == 0 and result["effects"]["concentration"] is not None:
            result["effects"]["concentration"] = None
            concentration = {
                "dc": None,
                "roll": None,
                "modifier": result["derived"]["saving_throws"]["constitution"],
                "maintained": False,
                "reason": "unconscious",
            }
        elif damage > 0 and result["effects"]["concentration"] is not None:
            dc = min(30, max(10, damage // 2))
            roll_value = roll("1d20").total
            save_bonus = result["derived"]["saving_throws"]["constitution"]
            maintained = roll_value + save_bonus >= dc
            concentration = {
                "dc": dc,
                "roll": roll_value,
                "modifier": save_bonus,
                "maintained": maintained,
            }
            if not maintained:
                result["effects"]["concentration"] = None
        return result, {
            "death_failures": death_failures,
            "death_status": saves["status"],
            "concentration_check": concentration,
        }

    def on_healed(self, character: dict[str, Any]) -> dict[str, Any]:
        result = self.sync(character)
        if result["hp"] > 0:
            self._reset_death_saves(result)
        return result

    def start_concentration(
        self, character: dict[str, Any], effect_id: str, name: str
    ) -> dict[str, Any]:
        if not effect_id or len(effect_id) > 120 or not name or len(name) > 120:
            raise ResourceValidationError("Concentration effect kimligi ve adi gecersiz.")
        result = self.sync(character)
        if (
            result["hp"] <= 0
            or result["resource_state"]["death_saves"]["status"] == "dead"
            or any(
                condition["id"] == "condition:incapacitated"
                for condition in result["effects"]["conditions"]
            )
        ):
            raise ResourceValidationError(
                "Bu karakter concentration baslatamaz."
            )
        result["effects"]["concentration"] = {
            "effect_id": effect_id,
            "name": name,
        }
        return result

    def end_concentration(self, character: dict[str, Any]) -> dict[str, Any]:
        result = self.sync(character)
        result["effects"]["concentration"] = None
        return result

    def add_condition(
        self,
        character: dict[str, Any],
        condition_id: str,
        duration: dict[str, Any],
    ) -> dict[str, Any]:
        result = self.sync(character)
        try:
            entry = self.catalog.get_entry(
                result["ruleset_version"], condition_id
            )["entry"]
        except (KeyError, ValueError) as error:
            raise ResourceValidationError("Condition katalog kaydi bulunamadi.") from error
        if entry["type"] != "condition":
            raise ResourceValidationError("Kayit bir condition degil.")
        normalized_duration = self._duration(duration)
        conditions = result["effects"]["conditions"]
        conditions[:] = [
            condition for condition in conditions if condition["id"] != condition_id
        ]
        conditions.append(
            {
                "id": condition_id,
                "name": entry["name"],
                "duration": normalized_duration,
            }
        )
        if condition_id == "condition:incapacitated":
            result["effects"]["concentration"] = None
        return self.sync(result)

    def remove_condition(
        self, character: dict[str, Any], condition_id: str
    ) -> dict[str, Any]:
        result = self.sync(character)
        before = len(result["effects"]["conditions"])
        result["effects"]["conditions"] = [
            condition
            for condition in result["effects"]["conditions"]
            if condition["id"] != condition_id
        ]
        if len(result["effects"]["conditions"]) == before:
            raise ResourceValidationError("Condition aktif degil.")
        return self.sync(result)

    def tick_end_turn(self, character: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        result = self.sync(character)
        expired = []
        remaining = []
        for condition in result["effects"]["conditions"]:
            duration = condition["duration"]
            if duration["kind"] == "rounds" and duration["tick"] == "end_turn":
                duration["remaining"] -= 1
                if duration["remaining"] <= 0:
                    expired.append(condition["id"])
                    continue
            remaining.append(condition)
        result["effects"]["conditions"] = remaining
        return self.sync(result), expired

    def validate(self, character: dict[str, Any]) -> None:
        state = character.get("resource_state")
        effects = character.get("effects")
        if (
            not isinstance(state, dict)
            or set(state)
            != {"schema_version", "hit_dice", "class_resources", "death_saves"}
            or state.get("schema_version") != RESOURCE_SCHEMA_VERSION
        ):
            raise ResourceValidationError("Resource state schema gecersiz.")
        hit_dice = state.get("hit_dice")
        if (
            not isinstance(hit_dice, dict)
            or set(hit_dice) != {"die_size", "maximum", "remaining"}
            or hit_dice["die_size"] not in {4, 6, 8, 10, 12}
            or not all(
                isinstance(hit_dice[key], int) and not isinstance(hit_dice[key], bool)
                for key in ("maximum", "remaining")
            )
            or not 0 <= hit_dice["remaining"] <= hit_dice["maximum"] <= 20
        ):
            raise ResourceValidationError("Hit Dice state gecersiz.")
        saves = state.get("death_saves")
        if (
            not isinstance(saves, dict)
            or set(saves)
            != {"successes", "failures", "status", "last_rolled_turn"}
            or saves["status"] not in {"none", "active", "stable", "dead"}
            or not all(
                isinstance(saves[field], int)
                and not isinstance(saves[field], bool)
                for field in ("successes", "failures")
            )
            or not 0 <= saves["successes"] <= 2
            or not 0 <= saves["failures"] <= 3
            or (
                saves["last_rolled_turn"] is not None
                and (
                    not isinstance(saves["last_rolled_turn"], int)
                    or isinstance(saves["last_rolled_turn"], bool)
                    or saves["last_rolled_turn"] < 1
                )
            )
        ):
            raise ResourceValidationError("Death save state gecersiz.")
        resources = state.get("class_resources")
        if not isinstance(resources, dict) or len(resources) > 32:
            raise ResourceValidationError("Class resource state gecersiz.")
        for key, resource in resources.items():
            if (
                not isinstance(key, str)
                or not 1 <= len(key) <= 80
                or not isinstance(resource, dict)
                or set(resource)
                != {
                    "source_id",
                    "maximum",
                    "remaining",
                    "short_rest_recovery",
                    "long_rest_recovery",
                }
                or not isinstance(resource["source_id"], str)
                or not 1 <= len(resource["source_id"]) <= 120
                or not all(
                    isinstance(resource[field], int)
                    and not isinstance(resource[field], bool)
                    for field in ("maximum", "remaining", "short_rest_recovery")
                )
                or not 0 <= resource["remaining"] <= resource["maximum"] <= 100
                or not 0 <= resource["short_rest_recovery"] <= 100
                or resource["long_rest_recovery"] != "all"
            ):
                raise ResourceValidationError("Class resource state gecersiz.")
        if (
            not isinstance(effects, dict)
            or set(effects) != {"concentration", "conditions"}
            or not isinstance(effects["conditions"], list)
            or len(effects["conditions"]) > 32
        ):
            raise ResourceValidationError("Character effects state gecersiz.")
        concentration = effects["concentration"]
        if concentration is not None and (
            not isinstance(concentration, dict)
            or set(concentration) != {"effect_id", "name"}
            or not all(
                isinstance(concentration[field], str)
                and 1 <= len(concentration[field]) <= 120
                for field in ("effect_id", "name")
            )
        ):
            raise ResourceValidationError("Concentration state gecersiz.")
        seen_conditions: set[str] = set()
        for condition in effects["conditions"]:
            if (
                not isinstance(condition, dict)
                or set(condition) != {"id", "name", "duration"}
                or not isinstance(condition["id"], str)
                or not 1 <= len(condition["id"]) <= 120
                or not isinstance(condition["name"], str)
                or not 1 <= len(condition["name"]) <= 120
                or condition["id"] in seen_conditions
            ):
                raise ResourceValidationError("Condition state gecersiz.")
            seen_conditions.add(condition["id"])
            if self._duration(condition.get("duration")) != condition["duration"]:
                raise ResourceValidationError("Condition duration state gecersiz.")

    def _class_entry(self, character: dict[str, Any]) -> dict[str, Any] | None:
        class_id = character.get("class_id")
        if class_id is None:
            return None
        return self.catalog.get_entry(character["ruleset_version"], class_id)["entry"]

    @staticmethod
    def _second_wind_max(
        character: dict[str, Any], feature: dict[str, Any]
    ) -> int:
        level = int(character["level"])
        tiers = feature["data"]["uses_by_level"]
        return int(
            tiers[
                max(
                    (threshold for threshold in tiers if int(threshold) <= level),
                    key=int,
                )
            ]
        )

    @staticmethod
    def _reset_death_saves(character: dict[str, Any]) -> None:
        last_rolled_turn = character["resource_state"]["death_saves"].get(
            "last_rolled_turn"
        )
        character["resource_state"]["death_saves"] = {
            "successes": 0,
            "failures": 0,
            "status": "none",
            "last_rolled_turn": last_rolled_turn,
        }

    @staticmethod
    def _duration(duration: Any) -> dict[str, Any]:
        if not isinstance(duration, dict):
            raise ResourceValidationError("Condition duration bir obje olmali.")
        kind = duration.get("kind")
        if kind == "permanent":
            return {"kind": "permanent"}
        if kind in {"short_rest", "long_rest"}:
            return {"kind": kind}
        if kind == "rounds":
            remaining = duration.get("remaining")
            tick = duration.get("tick", "end_turn")
            if (
                not isinstance(remaining, int)
                or isinstance(remaining, bool)
                or not 1 <= remaining <= 10_000
                or tick not in {"end_turn"}
            ):
                raise ResourceValidationError("Round condition duration gecersiz.")
            return {"kind": "rounds", "remaining": remaining, "tick": tick}
        raise ResourceValidationError("Condition duration kind gecersiz.")

    @staticmethod
    def _migrate_conditions(names: list[Any]) -> list[dict[str, Any]]:
        result = []
        for raw_name in names[:32]:
            name = str(raw_name)[:120]
            slug = name.strip().casefold().replace(" ", "-")
            result.append(
                {
                    "id": f"condition:{slug}",
                    "name": name,
                    "duration": {"kind": "permanent"},
                }
            )
        return result

    @staticmethod
    def _expire_rest_conditions(character: dict[str, Any], rest: str) -> None:
        expiring = {"short_rest"} if rest == "short" else {"short_rest", "long_rest"}
        character["effects"]["conditions"] = [
            condition
            for condition in character["effects"]["conditions"]
            if condition["duration"]["kind"] not in expiring
        ]
