import unittest

from api.character_engine import CharacterEngine, CharacterValidationError


class CharacterEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = CharacterEngine()
        self.character = self.engine.new_character("char-1", "member-1", "Riva")

    def test_new_character_separates_inputs_and_deterministic_derived_values(self):
        self.assertEqual(self.character["schema_version"], 2)
        self.assertEqual(self.character["inputs"]["ability_scores"]["strength"], 10)
        self.assertEqual(self.character["derived"]["ability_modifiers"]["strength"], 0)
        self.assertEqual(self.character["derived"]["proficiency_bonus"], 2)
        self.assertEqual(self.character["derived"]["saving_throws"]["strength"], 2)
        self.assertEqual(self.character["derived"]["saving_throws"]["constitution"], 2)
        self.assertEqual(self.character["derived"]["saving_throws"]["wisdom"], 0)
        self.assertEqual(self.character["derived"]["armor_class"], 10)
        self.assertEqual(self.character["derived"]["max_hp"], 10)
        self.assertEqual(self.character["derived"]["speed"], 30)

    def test_negative_modifier_uses_floor_and_proficiency_scales_by_level(self):
        updated = self.engine.update(
            self.character,
            {
                "level": 9,
                "inputs": {
                    "ability_scores": {
                        "strength": 9,
                        "dexterity": 17,
                        "constitution": 14,
                    }
                },
            },
        )

        self.assertEqual(updated["derived"]["ability_modifiers"]["strength"], -1)
        self.assertEqual(updated["derived"]["ability_modifiers"]["dexterity"], 3)
        self.assertEqual(updated["derived"]["proficiency_bonus"], 4)
        self.assertEqual(updated["derived"]["saving_throws"]["strength"], 3)
        self.assertEqual(updated["derived"]["initiative"], 3)

    def test_all_proficiency_bonus_tier_boundaries(self):
        expected = {1: 2, 4: 2, 5: 3, 8: 3, 9: 4, 12: 4, 13: 5, 16: 5, 17: 6, 20: 6}
        for level, bonus in expected.items():
            with self.subTest(level=level):
                updated = self.engine.update(self.character, {"level": level})
                self.assertEqual(updated["derived"]["proficiency_bonus"], bonus)

    def test_skill_proficiency_expertise_and_passive_perception(self):
        updated = self.engine.update(
            self.character,
            {
                "inputs": {
                    "ability_scores": {"wisdom": 16, "dexterity": 14},
                    "skill_proficiencies": ["perception", "stealth"],
                    "skill_expertise": ["perception"],
                }
            },
        )

        self.assertEqual(updated["derived"]["skills"]["perception"], 7)
        self.assertEqual(updated["derived"]["skills"]["stealth"], 4)
        self.assertEqual(updated["derived"]["passive_perception"], 17)

    def test_ac_hp_and_speed_policies_recalculate_and_cap_current_hp(self):
        damaged = dict(self.character)
        damaged["hp"] = 9
        updated = self.engine.update(
            damaged,
            {
                "level": 3,
                "inputs": {
                    "ability_scores": {"dexterity": 18, "constitution": 14},
                    "armor_class": {
                        "base": 14,
                        "dexterity_cap": 2,
                        "bonus": 2,
                    },
                    "hit_points": {
                        "level_one_base": 10,
                        "per_level_base": 6,
                        "constitution_per_level": True,
                        "bonus": 1,
                    },
                    "speed": {"bonus": 10},
                },
            },
        )

        self.assertEqual(updated["derived"]["armor_class"], 18)
        self.assertEqual(updated["derived"]["max_hp"], 29)
        self.assertEqual(updated["derived"]["speed"], 40)
        self.assertEqual(updated["hp"], 9)

        reduced = self.engine.update(
            updated,
            {"inputs": {"hit_points": {"bonus": -20}}},
        )
        self.assertEqual(reduced["derived"]["max_hp"], 8)
        self.assertEqual(reduced["hp"], 8)

    def test_each_level_grants_at_least_one_hp_after_constitution(self):
        updated = self.engine.update(
            self.character,
            {
                "level": 4,
                "inputs": {
                    "ability_scores": {"constitution": 1},
                    "hit_points": {
                        "level_one_base": 4,
                        "per_level_base": 1,
                        "constitution_per_level": True,
                        "bonus": 0,
                    },
                },
            },
        )
        self.assertEqual(updated["derived"]["max_hp"], 4)

    def test_ac_handles_negative_dexterity_and_can_disable_dexterity(self):
        negative = self.engine.update(
            self.character,
            {
                "inputs": {
                    "ability_scores": {"dexterity": 6},
                    "armor_class": {
                        "base": 14,
                        "add_dexterity": True,
                        "dexterity_cap": 2,
                    },
                }
            },
        )
        heavy = self.engine.update(
            negative,
            {"inputs": {"armor_class": {"add_dexterity": False}}},
        )
        self.assertEqual(negative["derived"]["armor_class"], 12)
        self.assertEqual(heavy["derived"]["armor_class"], 14)

    def test_rejects_derived_writes_invalid_expertise_and_wrong_catalog_type(self):
        with self.assertRaises(CharacterValidationError):
            self.engine.update(self.character, {"derived": {"armor_class": 99}})
        with self.assertRaises(CharacterValidationError):
            self.engine.update(
                self.character,
                {"inputs": {"skill_expertise": ["arcana"]}},
            )
        with self.assertRaises(CharacterValidationError):
            self.engine.update(
                self.character,
                {"class_id": "species:human"},
            )
        wrong_ruleset = dict(self.character)
        wrong_ruleset["ruleset_version"] = "srd-9"
        with self.assertRaises(CharacterValidationError):
            self.engine.recalculate(wrong_ruleset)

    def test_rejects_bool_or_out_of_range_ability_scores(self):
        for value in (True, 0, 31, 10.5):
            with self.subTest(value=value), self.assertRaises(CharacterValidationError):
                self.engine.update(
                    self.character,
                    {"inputs": {"ability_scores": {"strength": value}}},
                )


if __name__ == "__main__":
    unittest.main()
