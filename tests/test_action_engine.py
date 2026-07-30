import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.action_engine import ActionEngine, ActionValidationError
from api.character_engine import CharacterEngine


class ActionEngineTest(unittest.TestCase):
    def setUp(self):
        self.character_engine = CharacterEngine()
        self.character = self.character_engine.new_character(
            "character-1", "member-1", "Riva"
        )
        self.engine = ActionEngine(self.character_engine.catalog)

    def configured(self):
        return self.engine.configure(
            self.character,
            ability="wisdom",
            known_spell_ids=["spell:cure-wounds"],
            prepared_spell_ids=["spell:cure-wounds"],
            slots={"1": 2, "2": 1},
            attacks=[
                {
                    "id": "longsword",
                    "name": "Longsword",
                    "ability": "strength",
                    "proficient": True,
                    "damage_dice": "1d8+2",
                    "damage_type": "slashing",
                }
            ],
        )

    def test_configuration_preserves_spent_slots_and_long_rest_recovers(self):
        configured = self.configured()
        configured["action_state"]["spellcasting"]["slots"]["1"]["remaining"] = 1
        reconfigured = self.engine.configure(
            configured,
            ability="wisdom",
            known_spell_ids=["spell:cure-wounds"],
            prepared_spell_ids=["spell:cure-wounds"],
            slots={"1": 3},
            attacks=[],
        )
        self.assertEqual(
            reconfigured["action_state"]["spellcasting"]["slots"]["1"],
            {"maximum": 3, "remaining": 2},
        )
        rested, report = self.engine.long_rest(reconfigured)
        self.assertEqual(rested["action_state"]["spellcasting"]["slots"]["1"]["remaining"], 3)
        self.assertEqual(report["spell_slots_recovered"]["1"], 1)

    def test_cast_cure_wounds_consumes_slot_and_emits_typed_intent(self):
        configured = self.configured()
        updated, resolved = self.engine.cast_spell(
            configured, "spell:cure-wounds", 2, self.character
        )
        self.assertEqual(
            updated["action_state"]["spellcasting"]["slots"]["2"]["remaining"], 0
        )
        self.assertEqual(resolved["intent"]["kind"], "spell")
        self.assertEqual(resolved["intent"]["source_id"], "spell:cure-wounds")
        self.assertEqual(resolved["healing"]["expression"], "4d8")

    def test_checks_and_attacks_use_derived_modifiers_not_client_values(self):
        configured = self.configured()
        check = self.engine.roll_check(configured, "skill", "athletics", "advantage")
        self.assertEqual(check["intent"]["roll"]["expression"], "2d20kh1")
        attack = self.engine.attack(configured, "longsword", self.character, "normal")
        self.assertEqual(attack["intent"]["roll"]["modifier"], 2)
        self.assertIn("hit", attack)

    def test_rejects_unprepared_spell_and_malformed_attack_dice(self):
        configured = self.configured()
        configured["action_state"]["spellcasting"]["prepared_spell_ids"] = []
        with self.assertRaises(ActionValidationError):
            self.engine.cast_spell(
                configured, "spell:cure-wounds", 1, self.character
            )
        with self.assertRaises(ActionValidationError):
            self.engine.configure(
                self.character,
                ability=None,
                known_spell_ids=[],
                prepared_spell_ids=[],
                slots={},
                attacks=[
                    {
                        "id": "bad",
                        "name": "Bad",
                        "ability": "strength",
                        "proficient": True,
                        "damage_dice": "100d1000",
                        "damage_type": "force",
                    }
                ],
            )

    @patch("api.action_engine.roll")
    def test_negative_damage_and_healing_totals_are_clamped_to_zero(
        self, mock_roll
    ):
        configured = self.configured()
        mock_roll.side_effect = [
            SimpleNamespace(
                expression="1d20+2",
                rolls=(10,),
                kept=(10,),
                modifier=2,
                total=12,
            ),
            SimpleNamespace(
                expression="1d8-99999",
                rolls=(1,),
                kept=(1,),
                modifier=-99999,
                total=-99998,
            ),
            SimpleNamespace(
                expression="2d8-5",
                rolls=(1, 1),
                kept=(1, 1),
                modifier=-5,
                total=-3,
            ),
        ]
        configured["action_state"]["attacks"]["longsword"][
            "damage_dice"
        ] = "1d8-99999"
        attack = self.engine.attack(
            configured, "longsword", self.character, "normal"
        )
        configured["derived"]["ability_modifiers"]["wisdom"] = -5
        _, spell = self.engine.cast_spell(
            configured, "spell:cure-wounds", 1, self.character
        )

        self.assertTrue(attack["hit"])
        self.assertEqual(attack["damage"]["total"], 0)
        self.assertEqual(spell["healing"]["total"], 0)

    def test_action_schema_and_duplicate_attack_ids_are_strict(self):
        corrupted = self.configured()
        corrupted["action_state"]["injected"] = True
        with self.assertRaises(ActionValidationError):
            self.engine.sync(corrupted)
        corrupted = self.configured()
        corrupted["action_state"]["schema_version"] = True
        with self.assertRaises(ActionValidationError):
            self.engine.sync(corrupted)

        duplicate = {
            "id": "same",
            "name": "Same",
            "ability": "strength",
            "proficient": True,
            "damage_dice": "1d4",
            "damage_type": "slashing",
        }
        with self.assertRaisesRegex(ActionValidationError, "tekrar"):
            self.engine.configure(
                self.character,
                ability=None,
                known_spell_ids=[],
                prepared_spell_ids=[],
                slots={},
                attacks=[duplicate, duplicate],
            )
        with self.assertRaises(ActionValidationError):
            self.engine.configure(
                self.character,
                ability=None,
                known_spell_ids=[],
                prepared_spell_ids=[],
                slots={},
                attacks=["not-an-object"],
            )

    @patch("api.action_engine.roll")
    def test_attack_natural_one_misses_and_natural_twenty_critically_hits(
        self, mock_roll
    ):
        configured = self.configured()
        mock_roll.return_value = SimpleNamespace(
            expression="1d20+2",
            rolls=(1,),
            kept=(1,),
            modifier=2,
            total=3,
        )
        miss = self.engine.attack(
            configured, "longsword", self.character, "normal"
        )

        mock_roll.side_effect = [
            SimpleNamespace(
                expression="1d20+2",
                rolls=(20,),
                kept=(20,),
                modifier=2,
                total=22,
            ),
            SimpleNamespace(
                expression="2d8+2",
                rolls=(4, 5),
                kept=(4, 5),
                modifier=2,
                total=11,
            ),
        ]
        mock_roll.return_value = None
        critical = self.engine.attack(
            configured, "longsword", self.character, "normal"
        )

        self.assertTrue(miss["automatic_miss"])
        self.assertFalse(miss["hit"])
        self.assertIsNone(miss["damage"])
        self.assertTrue(critical["critical"])
        self.assertTrue(critical["hit"])
        self.assertEqual(critical["damage"]["expression"], "2d8+2")


if __name__ == "__main__":
    unittest.main()
