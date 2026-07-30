import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.character_engine import CharacterEngine
from api.resource_engine import ResourceEngine, ResourceValidationError


class ResourceEngineTest(unittest.TestCase):
    def setUp(self):
        self.character = CharacterEngine().new_character("char-1", "member-1", "Riva")
        self.engine = ResourceEngine()

    def test_initial_hit_dice_and_second_wind_follow_catalog(self):
        hit_dice = self.character["resource_state"]["hit_dice"]
        second_wind = self.character["resource_state"]["class_resources"]["second-wind"]
        self.assertEqual(hit_dice, {"die_size": 10, "maximum": 1, "remaining": 1})
        self.assertEqual(second_wind["source_id"], "feature:second-wind")
        self.assertEqual(second_wind["remaining"], 2)

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=6))
    def test_short_rest_spends_hit_die_and_recovers_one_second_wind(self, _roll):
        character = self.engine.sync(self.character)
        character["resource_state"]["class_resources"]["second-wind"]["remaining"] = 0
        character["hp"] = 2
        rested, result = self.engine.short_rest(character, 1)
        self.assertEqual(rested["hp"], 8)
        self.assertEqual(rested["resource_state"]["hit_dice"]["remaining"], 0)
        self.assertEqual(
            rested["resource_state"]["class_resources"]["second-wind"]["remaining"], 1
        )
        self.assertEqual(result["rolls"][0]["healing"], 6)

    def test_short_rest_requires_positive_hp_and_available_hit_dice(self):
        unconscious = dict(self.character)
        unconscious["hp"] = 0
        with self.assertRaises(ResourceValidationError):
            self.engine.short_rest(unconscious, 0)
        with self.assertRaises(ResourceValidationError):
            self.engine.short_rest(self.character, 2)

    def test_long_rest_restores_hp_hit_dice_resources_and_ends_concentration(self):
        character = self.engine.sync(self.character)
        character["resource_state"]["class_resources"]["second-wind"]["remaining"] = 1
        character["resource_state"]["hit_dice"]["remaining"] = 0
        character["hp"] = 1
        character = self.engine.start_concentration(character, "spell:shield", "Shield")
        rested, result = self.engine.long_rest(character)
        self.assertEqual(rested["hp"], rested["max_hp"])
        self.assertEqual(rested["resource_state"]["hit_dice"]["remaining"], 1)
        self.assertEqual(
            rested["resource_state"]["class_resources"]["second-wind"]["remaining"], 2
        )
        self.assertIsNone(rested["effects"]["concentration"])
        self.assertEqual(result["hit_dice_recovered"], 1)

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=7))
    def test_second_wind_is_typed_scaled_and_atomic(self, _roll):
        character = self.engine.sync(self.character)
        character["hp"] = 1
        used, result = self.engine.use_second_wind(character)
        self.assertEqual(result, {"roll": 7, "modifier": 1, "healing": 8, "remaining": 1})
        self.assertEqual(used["hp"], 9)
        with self.assertRaisesRegex(ResourceValidationError, "use_second_wind"):
            self.engine.expend_resource(used, "second-wind")

    def test_second_wind_and_hit_dice_reconcile_on_level_and_class_change(self):
        character = self.engine.sync(self.character)
        character["resource_state"]["class_resources"]["second-wind"]["remaining"] = 1
        character["resource_state"]["hit_dice"]["remaining"] = 0
        character["level"] = 4
        leveled = self.engine.sync(character)
        self.assertEqual(
            leveled["resource_state"]["class_resources"]["second-wind"]["maximum"], 3
        )
        self.assertEqual(
            leveled["resource_state"]["class_resources"]["second-wind"]["remaining"], 2
        )
        self.assertEqual(leveled["resource_state"]["hit_dice"]["remaining"], 3)
        leveled["class_id"] = None
        changed = self.engine.sync(leveled)
        self.assertEqual(changed["resource_state"]["class_resources"], {})
        self.assertEqual(changed["resource_state"]["hit_dice"]["die_size"], 8)

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=20))
    def test_natural_twenty_death_save_restores_one_hp(self, _roll):
        character = dict(self.character)
        character["hp"] = 0
        saved, result = self.engine.death_save(character, 1)
        self.assertEqual(saved["hp"], 1)
        self.assertEqual(result["outcome"], "revived")
        self.assertEqual(saved["resource_state"]["death_saves"]["status"], "none")

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=1))
    def test_natural_one_death_save_adds_two_failures(self, _roll):
        character = dict(self.character)
        character["hp"] = 0
        saved, result = self.engine.death_save(character, 1)
        self.assertEqual(result["outcome"], "double_failure")
        self.assertEqual(saved["resource_state"]["death_saves"]["failures"], 2)

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=5))
    def test_damage_concentration_check_uses_bounded_half_damage_dc(self, _roll):
        character = self.engine.start_concentration(
            self.character, "spell:cure-wounds", "Test Effect"
        )
        checked, result = self.engine.apply_damage_state(character, 100)
        self.assertEqual(result["concentration_check"]["dc"], 30)
        self.assertFalse(result["concentration_check"]["maintained"])
        self.assertIsNone(checked["effects"]["concentration"])

    def test_damage_at_zero_adds_failures_and_healing_resets_them(self):
        character = dict(self.character)
        character["hp"] = 0
        damaged, result = self.engine.apply_damage_state(
            character, 1, critical=True, was_at_zero=True
        )
        self.assertEqual(result["death_failures"], 2)
        damaged["hp"] = 1
        healed = self.engine.on_healed(damaged)
        self.assertEqual(healed["resource_state"]["death_saves"]["status"], "none")
        self.assertEqual(healed["resource_state"]["death_saves"]["failures"], 0)

    def test_dead_state_is_monotonic_under_more_damage(self):
        character = self.engine.sync(self.character)
        character["hp"] = 0
        character["resource_state"]["death_saves"]["status"] = "dead"
        damaged, result = self.engine.apply_damage_state(
            character, 1, was_at_zero=True
        )
        self.assertEqual(result["death_status"], "dead")
        self.assertEqual(damaged["resource_state"]["death_saves"]["status"], "dead")

    def test_unconscious_or_incapacitated_character_cannot_concentrate(self):
        unconscious = self.engine.sync(self.character)
        unconscious["hp"] = 0
        with self.assertRaises(ResourceValidationError):
            self.engine.start_concentration(unconscious, "effect:test", "Test")
        incapacitated = self.engine.sync(self.character)
        incapacitated["effects"]["conditions"].append(
            {
                "id": "condition:incapacitated",
                "name": "Incapacitated",
                "duration": {"kind": "permanent"},
            }
        )
        with self.assertRaises(ResourceValidationError):
            self.engine.start_concentration(
                incapacitated, "effect:test", "Test"
            )

    def test_round_and_rest_condition_durations_expire(self):
        round_condition = self.engine.add_condition(
            self.character,
            "condition:blinded",
            {"kind": "rounds", "remaining": 1, "tick": "end_turn"},
        )
        ticked, expired = self.engine.tick_end_turn(round_condition)
        self.assertEqual(expired, ["condition:blinded"])
        self.assertEqual(ticked["conditions"], [])

        rest_condition = self.engine.add_condition(
            self.character,
            "condition:blinded",
            {"kind": "short_rest"},
        )
        rested, _ = self.engine.short_rest(rest_condition, 0)
        self.assertEqual(rested["conditions"], [])


if __name__ == "__main__":
    unittest.main()
