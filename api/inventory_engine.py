from __future__ import annotations

from copy import deepcopy
import math
from typing import Any
from uuid import uuid4

from api.rules_catalog import RulesCatalog


INVENTORY_SCHEMA_VERSION = 1
MAX_INVENTORY_ENTRIES = 500
MAX_CONTAINER_DEPTH = 32
CURRENCY_DENOMINATIONS = ("cp", "sp", "ep", "gp", "pp")
EQUIPMENT_SLOTS = {
    "armor",
    "main_hand",
    "off_hand",
    "head",
    "neck",
    "shoulders",
    "torso",
    "hands",
    "waist",
    "feet",
    "ring",
    "other",
}
ENTRY_KEYS = {
    "id",
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
    "equipped",
    "attuned",
}


class InventoryValidationError(ValueError):
    pass


class InventoryEngine:
    def __init__(self, catalog: RulesCatalog | None = None):
        self.catalog = catalog or RulesCatalog()

    def initialize(self, character: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(character)
        legacy_items = list(result.get("inventory", []))
        result["inventory_state"] = {
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "entries": {},
            "currency": {denomination: 0 for denomination in CURRENCY_DENOMINATIONS},
            "encumbrance_policy": "standard",
            "derived": {},
        }
        catalog_items = self.catalog.list_entries(
            result["ruleset_version"], entity_type="item", limit=100
        )["entries"]
        by_name = {entry["name"].casefold(): entry["id"] for entry in catalog_items}
        for raw_name in legacy_items[:500]:
            name = str(raw_name).strip()[:120] or "Legacy Item"
            catalog_id = by_name.get(name.casefold())
            result = self.add_item(
                result,
                item_id=uuid4().hex,
                catalog_id=catalog_id,
                name=name if catalog_id is None else None,
                quantity=1,
                unit_weight_lb=0,
                unit_cost_gp=0,
                allow_rules_fields=False,
            )
        return self.sync(result)

    def sync(self, character: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(character)
        self.validate(result)
        state = result["inventory_state"]
        entries = state["entries"]
        for entry in entries.values():
            if entry["catalog_id"] is not None:
                catalog_entry = self._catalog_item(
                    result["ruleset_version"], entry["catalog_id"]
                )
                self._apply_catalog_fields(entry, catalog_entry)
        if (
            result.get("resource_state", {})
            .get("death_saves", {})
            .get("status")
            == "dead"
        ):
            for entry in entries.values():
                entry["attuned"] = False

        self._validate_relationships(entries)
        self._validate_equipment(entries)
        self._validate_attunement(entries)
        self._validate_container_capacities(entries)

        total_item_weight = sum(
            entry["quantity"] * entry["unit_weight_lb"]
            for entry in entries.values()
        )
        coin_count = sum(state["currency"].values())
        coin_weight = coin_count / 50
        strength = result["inputs"]["ability_scores"]["strength"]
        carrying_capacity = strength * 15
        armor_training = self._armor_training(result)
        armor_class_bonus = sum(
            entry["armor_class_bonus"]
            for entry in entries.values()
            if entry["equipped"]
            and (
                entry["armor_training"] is None
                or entry["armor_training"] in armor_training
            )
        )
        untrained_equipment = [
            entry["id"]
            for entry in entries.values()
            if entry["equipped"]
            and entry["armor_training"] is not None
            and entry["armor_training"] not in armor_training
        ]
        total_weight = round(total_item_weight + coin_weight, 3)
        state["derived"] = {
            "item_weight_lb": round(total_item_weight, 3),
            "coin_weight_lb": round(coin_weight, 3),
            "total_weight_lb": total_weight,
            "carrying_capacity_lb": carrying_capacity,
            "over_capacity": (
                state["encumbrance_policy"] == "standard"
                and total_weight > carrying_capacity
            ),
            "attuned_count": sum(
                1 for entry in entries.values() if entry["attuned"]
            ),
            "armor_class_bonus": armor_class_bonus,
            "untrained_equipment": untrained_equipment,
        }
        result["inventory"] = [entry["name"] for entry in entries.values()]
        return result

    def add_item(
        self,
        character: dict[str, Any],
        *,
        item_id: str,
        catalog_id: str | None = None,
        name: str | None = None,
        quantity: int = 1,
        unit_weight_lb: float = 0,
        unit_cost_gp: float = 0,
        equipment_slot: str | None = None,
        armor_training: str | None = None,
        armor_class_bonus: float = 0,
        container_capacity_lb: float | None = None,
        requires_attunement: bool = False,
        container_id: str | None = None,
        allow_rules_fields: bool = False,
    ) -> dict[str, Any]:
        result = self.sync(character)
        entries = result["inventory_state"]["entries"]
        if (
            not isinstance(item_id, str)
            or not 8 <= len(item_id) <= 64
            or item_id in entries
        ):
            raise InventoryValidationError("Inventory item kimligi gecersiz.")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 1_000_000:
            raise InventoryValidationError("Item quantity 1..1000000 arasinda olmali.")
        if catalog_id is not None:
            catalog_entry = self._catalog_item(result["ruleset_version"], catalog_id)
            entry = self._entry_from_catalog(item_id, catalog_entry, quantity)
        else:
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
                raise InventoryValidationError("Custom item adi gecersiz.")
            if not allow_rules_fields and (
                equipment_slot is not None
                or armor_training is not None
                or armor_class_bonus != 0
                or requires_attunement
            ):
                raise InventoryValidationError(
                    "Custom rules alanlari active DM yetkisi gerektirir."
                )
            entry = {
                "id": item_id,
                "catalog_id": None,
                "name": name.strip(),
                "quantity": quantity,
                "unit_weight_lb": self._number(
                    unit_weight_lb, "unit_weight_lb", 0, 1_000_000
                ),
                "unit_cost_gp": self._number(
                    unit_cost_gp, "unit_cost_gp", 0, 1_000_000_000
                ),
                "equipment_slot": self._slot(equipment_slot),
                "armor_training": self._optional_text(
                    armor_training, "armor_training", 40
                ),
                "armor_class_bonus": self._number(
                    armor_class_bonus, "armor_class_bonus", 0, 20
                ),
                "container_capacity_lb": self._optional_number(
                    container_capacity_lb,
                    "container_capacity_lb",
                    0.001,
                    1_000_000,
                ),
                "container_id": container_id,
                "requires_attunement": self._boolean(
                    requires_attunement, "requires_attunement"
                ),
                "equipped": False,
                "attuned": False,
            }
        entry["container_id"] = container_id
        entries[item_id] = entry
        return self.sync(result)

    def set_quantity(
        self, character: dict[str, Any], item_id: str, quantity: int
    ) -> dict[str, Any]:
        result = self.sync(character)
        entry = self._entry(result, item_id)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 1_000_000:
            raise InventoryValidationError("Item quantity 1..1000000 arasinda olmali.")
        if quantity != 1 and (
            entry["equipped"]
            or entry["attuned"]
            or entry["container_capacity_lb"] is not None
        ):
            raise InventoryValidationError(
                "Equipped, attuned veya container item stack olamaz."
            )
        entry["quantity"] = quantity
        return self.sync(result)

    def move_item(
        self, character: dict[str, Any], item_id: str, container_id: str | None
    ) -> dict[str, Any]:
        result = self.sync(character)
        entry = self._entry(result, item_id)
        if entry["equipped"]:
            raise InventoryValidationError("Equipped item container'a tasinamaz.")
        entry["container_id"] = container_id
        return self.sync(result)

    def remove_item(self, character: dict[str, Any], item_id: str) -> dict[str, Any]:
        result = self.sync(character)
        entry = self._entry(result, item_id)
        if entry["equipped"] or entry["attuned"]:
            raise InventoryValidationError(
                "Item silinmeden once unequip ve unattune edilmeli."
            )
        if any(
            candidate["container_id"] == item_id
            for candidate in result["inventory_state"]["entries"].values()
        ):
            raise InventoryValidationError("Dolu container silinemez.")
        del result["inventory_state"]["entries"][item_id]
        return self.sync(result)

    def equip(self, character: dict[str, Any], item_id: str) -> dict[str, Any]:
        result = self.sync(character)
        entry = self._entry(result, item_id)
        if entry["equipment_slot"] is None:
            raise InventoryValidationError("Item equip edilemez.")
        if entry["container_id"] is not None:
            raise InventoryValidationError("Container icindeki item equip edilemez.")
        if entry["quantity"] != 1:
            raise InventoryValidationError("Stack item equip edilemez.")
        entry["equipped"] = True
        return self.sync(result)

    def unequip(self, character: dict[str, Any], item_id: str) -> dict[str, Any]:
        result = self.sync(character)
        entry = self._entry(result, item_id)
        if not entry["equipped"]:
            raise InventoryValidationError("Item equipped degil.")
        entry["equipped"] = False
        return self.sync(result)

    def attune(self, character: dict[str, Any], item_id: str) -> dict[str, Any]:
        result = self.sync(character)
        entry = self._entry(result, item_id)
        if not entry["requires_attunement"]:
            raise InventoryValidationError("Item attunement gerektirmiyor.")
        if entry["attuned"]:
            raise InventoryValidationError("Item zaten attuned.")
        if entry["quantity"] != 1:
            raise InventoryValidationError("Stack item attuned olamaz.")
        entry["attuned"] = True
        return self.sync(result)

    def unattune(self, character: dict[str, Any], item_id: str) -> dict[str, Any]:
        result = self.sync(character)
        entry = self._entry(result, item_id)
        if not entry["attuned"]:
            raise InventoryValidationError("Item attuned degil.")
        entry["attuned"] = False
        return self.sync(result)

    def end_all_attunement(self, character: dict[str, Any]) -> dict[str, Any]:
        result = self.sync(character)
        for entry in result["inventory_state"]["entries"].values():
            entry["attuned"] = False
        return self.sync(result)

    def adjust_currency(
        self, character: dict[str, Any], denomination: str, delta: int
    ) -> dict[str, Any]:
        result = self.sync(character)
        if denomination not in CURRENCY_DENOMINATIONS:
            raise InventoryValidationError("Currency denomination gecersiz.")
        if not isinstance(delta, int) or isinstance(delta, bool) or not -1_000_000_000 <= delta <= 1_000_000_000:
            raise InventoryValidationError("Currency delta gecersiz.")
        current = result["inventory_state"]["currency"][denomination]
        if not 0 <= current + delta <= 1_000_000_000:
            raise InventoryValidationError("Currency bakiyesi gecersiz.")
        result["inventory_state"]["currency"][denomination] += delta
        return self.sync(result)

    def set_encumbrance_policy(
        self, character: dict[str, Any], policy: str
    ) -> dict[str, Any]:
        result = self.sync(character)
        if policy not in {"standard", "ignore"}:
            raise InventoryValidationError("Encumbrance policy gecersiz.")
        result["inventory_state"]["encumbrance_policy"] = policy
        return self.sync(result)

    def validate(self, character: dict[str, Any]) -> None:
        state = character.get("inventory_state")
        if (
            not isinstance(state, dict)
            or set(state)
            != {
                "schema_version",
                "entries",
                "currency",
                "encumbrance_policy",
                "derived",
            }
            or state.get("schema_version") != INVENTORY_SCHEMA_VERSION
            or not isinstance(state["entries"], dict)
            or len(state["entries"]) > MAX_INVENTORY_ENTRIES
            or state["encumbrance_policy"] not in {"standard", "ignore"}
            or not isinstance(state["derived"], dict)
        ):
            raise InventoryValidationError("Inventory state schema gecersiz.")
        currency = state["currency"]
        if (
            not isinstance(currency, dict)
            or set(currency) != set(CURRENCY_DENOMINATIONS)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 1_000_000_000
                for value in currency.values()
            )
        ):
            raise InventoryValidationError("Currency state gecersiz.")
        for item_id, entry in state["entries"].items():
            if (
                not isinstance(item_id, str)
                or not isinstance(entry, dict)
                or set(entry) != ENTRY_KEYS
                or entry.get("id") != item_id
            ):
                raise InventoryValidationError("Inventory entry schema gecersiz.")
            self._validate_entry(entry)

    def _validate_entry(self, entry: dict[str, Any]) -> None:
        if (
            not 8 <= len(entry["id"]) <= 64
            or (
                entry["catalog_id"] is not None
                and (
                    not isinstance(entry["catalog_id"], str)
                    or not 1 <= len(entry["catalog_id"]) <= 80
                )
            )
            or not isinstance(entry["name"], str)
            or not 1 <= len(entry["name"]) <= 120
            or not isinstance(entry["quantity"], int)
            or isinstance(entry["quantity"], bool)
            or not 1 <= entry["quantity"] <= 1_000_000
            or self._slot(entry["equipment_slot"]) != entry["equipment_slot"]
            or self._optional_text(entry["armor_training"], "armor_training", 40)
            != entry["armor_training"]
            or any(
                not isinstance(entry[field], bool)
                for field in ("requires_attunement", "equipped", "attuned")
            )
        ):
            raise InventoryValidationError("Inventory entry degeri gecersiz.")
        self._number(entry["unit_weight_lb"], "unit_weight_lb", 0, 1_000_000)
        self._number(entry["unit_cost_gp"], "unit_cost_gp", 0, 1_000_000_000)
        self._number(entry["armor_class_bonus"], "armor_class_bonus", 0, 20)
        self._optional_number(
            entry["container_capacity_lb"],
            "container_capacity_lb",
            0.001,
            1_000_000,
        )
        if entry["container_id"] is not None and (
            not isinstance(entry["container_id"], str)
            or not 8 <= len(entry["container_id"]) <= 64
        ):
            raise InventoryValidationError("container_id gecersiz.")

    def _validate_relationships(self, entries: dict[str, dict[str, Any]]) -> None:
        for item_id, entry in entries.items():
            parent_id = entry["container_id"]
            if parent_id is not None:
                parent = entries.get(parent_id)
                if parent is None or parent["container_capacity_lb"] is None:
                    raise InventoryValidationError("Hedef container bulunamadi.")
                if parent_id == item_id:
                    raise InventoryValidationError("Item kendisini iceremez.")

        for item_id, entry in entries.items():
            parent_id = entry["container_id"]
            visited = {item_id}
            cursor = parent_id
            depth = 0
            while cursor is not None:
                if cursor in visited:
                    raise InventoryValidationError("Container dongusu olusturulamaz.")
                visited.add(cursor)
                cursor = entries[cursor]["container_id"]
                depth += 1
                if depth > MAX_CONTAINER_DEPTH:
                    raise InventoryValidationError(
                        "Container nesting limiti asildi."
                    )

    @staticmethod
    def _validate_equipment(entries: dict[str, dict[str, Any]]) -> None:
        slots: dict[str, int] = {}
        for entry in entries.values():
            if not entry["equipped"]:
                continue
            slot = entry["equipment_slot"]
            if (
                slot is None
                or entry["quantity"] != 1
                or entry["container_id"] is not None
            ):
                raise InventoryValidationError("Equipped item state gecersiz.")
            slots[slot] = slots.get(slot, 0) + 1
        for slot, count in slots.items():
            limit = (
                2
                if slot == "ring"
                else MAX_INVENTORY_ENTRIES
                if slot == "other"
                else 1
            )
            if count > limit:
                raise InventoryValidationError(f"{slot} equipment slot dolu.")

    @staticmethod
    def _validate_attunement(entries: dict[str, dict[str, Any]]) -> None:
        attuned = [entry for entry in entries.values() if entry["attuned"]]
        if len(attuned) > 3:
            raise InventoryValidationError("En fazla uc item attuned olabilir.")
        seen_catalog_ids: set[str] = set()
        for entry in attuned:
            if not entry["requires_attunement"] or entry["quantity"] != 1:
                raise InventoryValidationError("Attunement state gecersiz.")
            catalog_id = entry["catalog_id"]
            if catalog_id is not None:
                if catalog_id in seen_catalog_ids:
                    raise InventoryValidationError(
                        "Ayni magic item kopyasina iki kez attune olunamaz."
                    )
                seen_catalog_ids.add(catalog_id)

    def _validate_container_capacities(
        self, entries: dict[str, dict[str, Any]]
    ) -> None:
        children = {item_id: [] for item_id in entries}
        roots: list[str] = []
        for item_id, entry in entries.items():
            parent_id = entry["container_id"]
            if parent_id is None:
                roots.append(item_id)
            else:
                children[parent_id].append(item_id)

        # Relationships are validated immediately before this method, so the
        # graph is acyclic. Compute every subtree once instead of recursively
        # rescanning all entries for every container.
        subtree_weights: dict[str, float] = {}
        stack = [(item_id, False) for item_id in roots]
        while stack:
            item_id, expanded = stack.pop()
            if not expanded:
                stack.append((item_id, True))
                stack.extend((child_id, False) for child_id in children[item_id])
                continue
            entry = entries[item_id]
            subtree_weights[item_id] = (
                entry["quantity"] * entry["unit_weight_lb"]
                + sum(subtree_weights[child_id] for child_id in children[item_id])
            )

        for item_id, entry in entries.items():
            capacity = entry["container_capacity_lb"]
            if capacity is None:
                continue
            if entry["quantity"] != 1:
                raise InventoryValidationError("Container stack olamaz.")
            content_weight = sum(
                subtree_weights[child_id] for child_id in children[item_id]
            )
            if content_weight > capacity + 1e-9:
                raise InventoryValidationError("Container kapasitesi asildi.")

    def _armor_training(self, character: dict[str, Any]) -> set[str]:
        class_id = character.get("class_id")
        if class_id is None:
            return set()
        try:
            entry = self.catalog.get_entry(
                character["ruleset_version"], class_id
            )["entry"]
        except (KeyError, ValueError, TypeError) as error:
            raise InventoryValidationError(
                "Class katalog kaydi bulunamadi."
            ) from error
        if entry["type"] != "class":
            raise InventoryValidationError("Katalog kaydi class degil.")
        return set(entry["data"].get("armor_training", []))

    def _catalog_item(self, version: str, catalog_id: str) -> dict[str, Any]:
        if not isinstance(catalog_id, str) or not 1 <= len(catalog_id) <= 80:
            raise InventoryValidationError("Item katalog kimligi gecersiz.")
        try:
            entry = self.catalog.get_entry(version, catalog_id)["entry"]
        except (KeyError, ValueError) as error:
            raise InventoryValidationError("Item katalog kaydi bulunamadi.") from error
        if entry["type"] != "item":
            raise InventoryValidationError("Katalog kaydi item degil.")
        return entry

    @staticmethod
    def _apply_catalog_fields(
        entry: dict[str, Any], catalog_entry: dict[str, Any]
    ) -> None:
        data = catalog_entry["data"]
        entry.update(
            name=catalog_entry["name"],
            unit_weight_lb=data["weight_lb"],
            unit_cost_gp=data["cost_gp"],
            equipment_slot=data["equipment_slot"],
            armor_training=data["armor_training"],
            armor_class_bonus=data["armor_class_bonus"],
            container_capacity_lb=data["container_capacity_lb"],
            requires_attunement=data["requires_attunement"],
        )

    def _entry_from_catalog(
        self, item_id: str, catalog_entry: dict[str, Any], quantity: int
    ) -> dict[str, Any]:
        entry = {
            "id": item_id,
            "catalog_id": catalog_entry["id"],
            "name": catalog_entry["name"],
            "quantity": quantity,
            "unit_weight_lb": 0,
            "unit_cost_gp": 0,
            "equipment_slot": None,
            "armor_training": None,
            "armor_class_bonus": 0,
            "container_capacity_lb": None,
            "container_id": None,
            "requires_attunement": False,
            "equipped": False,
            "attuned": False,
        }
        self._apply_catalog_fields(entry, catalog_entry)
        return entry

    @staticmethod
    def _entry(character: dict[str, Any], item_id: str) -> dict[str, Any]:
        entry = character["inventory_state"]["entries"].get(item_id)
        if entry is None:
            raise InventoryValidationError("Inventory item bulunamadi.")
        return entry

    @staticmethod
    def _slot(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in EQUIPMENT_SLOTS:
            raise InventoryValidationError("Equipment slot gecersiz.")
        return value

    @staticmethod
    def _optional_text(value: Any, field: str, maximum: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
            raise InventoryValidationError(f"{field} gecersiz.")
        return value.strip()

    @staticmethod
    def _number(
        value: Any, field: str, minimum: float, maximum: float
    ) -> float | int:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not minimum <= value <= maximum
        ):
            raise InventoryValidationError(f"{field} gecersiz.")
        return value

    @classmethod
    def _optional_number(
        cls,
        value: Any,
        field: str,
        minimum: float,
        maximum: float,
    ) -> float | int | None:
        if value is None:
            return None
        return cls._number(value, field, minimum, maximum)

    @staticmethod
    def _boolean(value: Any, field: str) -> bool:
        if not isinstance(value, bool):
            raise InventoryValidationError(f"{field} boolean olmali.")
        return value
