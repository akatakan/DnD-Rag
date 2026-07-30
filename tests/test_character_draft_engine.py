import unittest

from api.character_draft_engine import (
    CharacterDraftEngine,
    CharacterDraftValidationError,
)
from api.character_engine import CharacterEngine


class CharacterDraftEngineTest(unittest.TestCase):
    def setUp(self):
        self.character_engine = CharacterEngine()
        self.engine = CharacterDraftEngine(self.character_engine)
        self.character = self.character_engine.new_character(
            "character-1", "owner-1", "Riva"
        )
        self.draft = self.engine.from_character(self.character)

    def test_patch_is_strict_and_step_validation_is_incremental(self):
        with self.assertRaises(CharacterDraftValidationError):
            self.engine.patch(self.draft, {"derived": {"armor_class": 99}})
        blank = self.engine.patch(self.draft, {"name": "   "})
        with self.assertRaises(CharacterDraftValidationError):
            self.engine.validate_step(blank, "basics", "srd-5.2.1")
        self.engine.validate_step(self.draft, "basics", "srd-5.2.1")

    def test_review_builds_fresh_authoritative_character(self):
        draft = self.engine.patch(
            self.draft,
            {
                "name": "Tess",
                "ability_scores": {
                    "strength": 16,
                    "dexterity": 12,
                    "constitution": 14,
                    "intelligence": 10,
                    "wisdom": 10,
                    "charisma": 8,
                },
                "skill_proficiencies": ["athletics", "insight", "religion"],
                "equipment_catalog_ids": ["item:shield"],
            },
        )
        self.engine.validate_step(draft, "review", "srd-5.2.1")
        built = self.engine.build_character(
            "character-1", "owner-1", "srd-5.2.1", draft
        )
        self.assertEqual(built["name"], "Tess")
        self.assertEqual(built["derived"]["ability_modifiers"]["strength"], 3)
        self.assertEqual(built["inventory"][0], "Shield")
        self.assertEqual(built["hp"], built["max_hp"])

    def test_expertise_and_catalog_types_fail_closed(self):
        invalid = self.engine.patch(
            self.draft,
            {
                "skill_proficiencies": [],
                "skill_expertise": ["athletics"],
            },
        )
        with self.assertRaises(CharacterDraftValidationError):
            self.engine.validate_step(invalid, "review", "srd-5.2.1")
        invalid = self.engine.patch(
            self.draft, {"equipment_catalog_ids": ["spell:cure-wounds"]}
        )
        with self.assertRaises(CharacterDraftValidationError):
            self.engine.validate_step(invalid, "equipment", "srd-5.2.1")

    def test_from_character_bounds_equipment_expansion_and_normalizes_slots(self):
        character = self.engine.inventory_engine.add_item(
            self.character,
            item_id="shield-many",
            catalog_id="item:shield",
            quantity=1_000_000,
        )
        character = self.engine.action_engine.configure(
            character,
            ability="wisdom",
            known_spell_ids=["spell:cure-wounds"],
            prepared_spell_ids=["spell:cure-wounds"],
            slots={"1": 2},
            attacks=[],
        )

        draft = self.engine.from_character(character)

        self.assertEqual(len(draft["equipment_catalog_ids"]), 50)
        self.assertEqual(
            set(draft["equipment_catalog_ids"]), {"item:shield"}
        )
        self.assertEqual(draft["spellcasting"]["slots"], {"1": 2})
        self.engine.validate_step(draft, "review", "srd-5.2.1")


if __name__ == "__main__":
    unittest.main()
