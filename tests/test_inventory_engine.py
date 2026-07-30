import unittest

from api.character_engine import CharacterEngine
from api.inventory_engine import InventoryEngine, InventoryValidationError


class InventoryEngineTest(unittest.TestCase):
    def setUp(self):
        self.character_engine = CharacterEngine()
        self.character = self.character_engine.new_character(
            "character-1", "member-1", "Riva"
        )
        self.engine = InventoryEngine(self.character_engine.catalog)

    def add_custom(self, character, item_id, **overrides):
        return self.engine.add_item(
            character,
            item_id=item_id,
            name=overrides.pop("name", item_id),
            allow_rules_fields=True,
            **overrides,
        )

    def test_initial_capacity_uses_strength_and_currency_has_weight(self):
        state = self.character["inventory_state"]
        self.assertEqual(state["derived"]["carrying_capacity_lb"], 150)
        adjusted = self.engine.adjust_currency(self.character, "gp", 50)
        self.assertEqual(adjusted["inventory_state"]["derived"]["coin_weight_lb"], 1)
        self.assertEqual(adjusted["inventory_state"]["derived"]["total_weight_lb"], 1)

    def test_catalog_shield_equip_updates_authoritative_ac(self):
        with_shield = self.engine.add_item(
            self.character,
            item_id="shield-0001",
            catalog_id="item:shield",
        )
        equipped = self.engine.equip(with_shield, "shield-0001")
        recalculated = self.character_engine.recalculate(equipped)
        self.assertEqual(
            recalculated["inventory_state"]["derived"]["armor_class_bonus"], 2
        )
        self.assertEqual(recalculated["ac"], 12)

        recalculated["class_id"] = None
        untrained = self.engine.sync(recalculated)
        self.assertEqual(
            untrained["inventory_state"]["derived"]["armor_class_bonus"], 0
        )
        self.assertEqual(
            untrained["inventory_state"]["derived"]["untrained_equipment"],
            ["shield-0001"],
        )

    def test_identity_quantity_and_delete_guards(self):
        first = self.add_custom(
            self.character,
            "rope-0001",
            name="Rope",
            quantity=2,
            unit_weight_lb=10,
        )
        second = self.add_custom(
            first,
            "rope-0002",
            name="Rope",
            quantity=1,
            unit_weight_lb=10,
        )
        changed = self.engine.set_quantity(second, "rope-0001", 3)
        removed = self.engine.remove_item(changed, "rope-0002")
        self.assertIn("rope-0001", removed["inventory_state"]["entries"])
        self.assertNotIn("rope-0002", removed["inventory_state"]["entries"])
        self.assertEqual(
            removed["inventory_state"]["derived"]["item_weight_lb"], 30
        )

    def test_container_capacity_and_cycle_are_rejected(self):
        bag = self.add_custom(
            self.character,
            "bag-00001",
            name="Bag",
            container_capacity_lb=10,
        )
        rock = self.add_custom(
            bag,
            "rock-0001",
            name="Rock",
            unit_weight_lb=6,
            container_id="bag-00001",
        )
        with self.assertRaisesRegex(InventoryValidationError, "kapasitesi"):
            self.add_custom(
                rock,
                "rock-0002",
                name="Rock",
                unit_weight_lb=6,
                container_id="bag-00001",
            )

        inner = self.add_custom(
            rock,
            "bag-00002",
            name="Inner Bag",
            container_capacity_lb=10,
            container_id="bag-00001",
        )
        with self.assertRaisesRegex(InventoryValidationError, "dongusu"):
            self.engine.move_item(inner, "bag-00001", "bag-00002")

    def test_attunement_limit_duplicate_and_remove_invariants(self):
        current = self.character
        for index in range(1, 5):
            current = self.add_custom(
                current,
                f"magic-00{index}",
                name=f"Magic Item {index}",
                requires_attunement=True,
            )
        for index in range(1, 4):
            current = self.engine.attune(current, f"magic-00{index}")
        self.assertEqual(current["inventory_state"]["derived"]["attuned_count"], 3)
        with self.assertRaisesRegex(InventoryValidationError, "uc item"):
            self.engine.attune(current, "magic-004")
        with self.assertRaisesRegex(InventoryValidationError, "unattune"):
            self.engine.remove_item(current, "magic-001")

    def test_encumbrance_policy_tracks_or_ignores_over_capacity(self):
        burdened = self.add_custom(
            self.character,
            "anvil-001",
            name="Anvil",
            unit_weight_lb=151,
        )
        self.assertTrue(burdened["inventory_state"]["derived"]["over_capacity"])
        ignored = self.engine.set_encumbrance_policy(burdened, "ignore")
        self.assertFalse(ignored["inventory_state"]["derived"]["over_capacity"])
        self.assertEqual(
            ignored["inventory_state"]["derived"]["total_weight_lb"], 151
        )


if __name__ == "__main__":
    unittest.main()
