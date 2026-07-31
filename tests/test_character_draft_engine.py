import unittest
from copy import deepcopy

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
        self.draft = self.engine.new_creation_draft(self.character)

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
                    "strength": 15,
                    "dexterity": 14,
                    "constitution": 13,
                    "intelligence": 8,
                    "wisdom": 10,
                    "charisma": 12,
                },
                "skill_proficiencies": [
                    "arcana",
                    "athletics",
                    "insight",
                    "perception",
                    "religion",
                ],
                "equipment_catalog_ids": ["item:shield"],
            },
        )
        self.engine.validate_step(draft, "review", "srd-5.2.1")
        built = self.engine.build_character(
            "character-1", "owner-1", "srd-5.2.1", draft
        )
        self.assertEqual(built["name"], "Tess")
        self.assertEqual(built["derived"]["ability_modifiers"]["strength"], 2)
        self.assertEqual(
            built["derived"]["ability_modifiers"]["intelligence"], 0
        )
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

    def test_creation_draft_bounds_equipment_and_clears_unsupported_spells(self):
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

        draft = self.engine.new_creation_draft(character)
        draft = self.engine.patch(
            draft,
            {
                "skill_proficiencies": [
                    "arcana",
                    "athletics",
                    "insight",
                    "perception",
                    "religion",
                ]
            },
        )

        self.assertEqual(len(draft["equipment_catalog_ids"]), 50)
        self.assertEqual(
            set(draft["equipment_catalog_ids"]), {"item:shield"}
        )
        self.assertEqual(
            draft["spellcasting"],
            {
                "ability": None,
                "known_spell_ids": [],
                "prepared_spell_ids": [],
                "slots": {},
            },
        )
        self.engine.validate_step(draft, "review", "srd-5.2.1")

        invalid = self.engine.patch(
            draft,
            {
                "spellcasting": {
                    "ability": "wisdom",
                    "known_spell_ids": ["spell:cure-wounds"],
                    "prepared_spell_ids": ["spell:cure-wounds"],
                    "slots": {"1": 4},
                }
            },
        )
        with self.assertRaisesRegex(
            CharacterDraftValidationError, "1. seviyede spellcasting"
        ):
            self.engine.validate_step(invalid, "spells", "srd-5.2.1")

    def test_ability_generation_and_background_rules_fail_closed(self):
        invalid_array = self.engine.patch(
            self.draft,
            {
                "ability_scores": {
                    ability: 15 for ability in self.draft["ability_scores"]
                },
            },
        )
        with self.assertRaisesRegex(
            CharacterDraftValidationError, "Standard Array"
        ):
            self.engine.validate_step(
                invalid_array, "abilities", "srd-5.2.1"
            )

        invalid_point_cost = self.engine.patch(
            self.draft,
            {
                "ability_score_method": "point_cost",
                "ability_scores": {
                    ability: 15 for ability in self.draft["ability_scores"]
                },
            },
        )
        with self.assertRaisesRegex(CharacterDraftValidationError, "27"):
            self.engine.validate_step(
                invalid_point_cost, "abilities", "srd-5.2.1"
            )

        invalid_background = self.engine.patch(
            self.draft,
            {"background_ability_increases": {"strength": 2, "wisdom": 1}},
        )
        with self.assertRaisesRegex(
            CharacterDraftValidationError, "katalog secenekleri"
        ):
            self.engine.validate_step(
                invalid_background, "background", "srd-5.2.1"
            )

    def test_fighter_human_acolyte_skill_choices_are_bounded(self):
        valid = self.engine.patch(
            self.draft,
            {
                "skill_proficiencies": [
                    "arcana",
                    "athletics",
                    "insight",
                    "perception",
                    "religion",
                ]
            },
        )
        self.engine.validate_step(valid, "proficiencies", "srd-5.2.1")

        for invalid_skills in (
            ["insight", "religion"],
            [
                "arcana",
                "athletics",
                "deception",
                "insight",
                "perception",
                "religion",
            ],
            ["arcana", "deception", "insight", "religion", "stealth"],
        ):
            with self.subTest(skills=invalid_skills):
                invalid = self.engine.patch(
                    self.draft,
                    {"skill_proficiencies": invalid_skills},
                )
                with self.assertRaises(CharacterDraftValidationError):
                    self.engine.validate_step(
                        invalid, "proficiencies", "srd-5.2.1"
                    )

    def test_quick_build_derives_a_complete_reviewable_draft(self):
        built = self.engine.quick_build(
            self.draft,
            "srd-5.2.1",
            name="  Riva  ",
            class_id="class:fighter",
            species_id="species:human",
        )

        self.assertEqual(built["name"], "Riva")
        self.assertEqual(built["class_id"], "class:fighter")
        self.assertEqual(built["species_id"], "species:human")
        self.assertEqual(built["background_id"], "background:acolyte")
        self.assertEqual(
            sorted(built["ability_scores"].values(), reverse=True),
            [15, 14, 13, 12, 10, 8],
        )
        self.assertEqual(
            sorted(built["background_ability_increases"].values(), reverse=True),
            [2, 1],
        )
        self.assertEqual(len(built["skill_proficiencies"]), 5)
        self.engine.validate_step(built, "review", "srd-5.2.1")

    def test_database_defined_class_drives_builder_and_hp_rules(self):
        catalog = self.character_engine.catalog
        admin = catalog.clone_ruleset(
            "srd-5.2.1", "srd-5.2.1-rogue.1", "SRD Rogue Test"
        )
        fighter = catalog.get_entry(
            "srd-5.2.1", "class:fighter"
        )["entry"]
        rogue = {
            key: deepcopy(value)
            for key, value in fighter.items()
            if key not in {"source", "license"}
        }
        rogue.update(
            {
                "id": "class:rogue",
                "slug": "rogue",
                "name": "Rogue",
                "data": {
                    **rogue["data"],
                    "hit_die": 8,
                    "primary_abilities": ["Dexterity"],
                    "saving_throw_proficiencies": [
                        "Dexterity", "Intelligence"
                    ],
                    "armor_training": ["Light"],
                    "starting_feature_ids": ["feature:rogue-training"],
                    "skill_proficiency_count": 2,
                    "skill_proficiency_options": [
                        "Acrobatics", "Investigation", "Stealth"
                    ],
                    "average_hp_per_level": 5,
                    "spellcasting": {
                        "ability": "Wisdom",
                        "spell_ids": ["spell:cure-wounds"],
                        "known_count_by_level": {"1": 1},
                        "prepared_count_by_level": {"1": 1},
                        "slots_by_level": {"1": {"1": 2}},
                    },
                },
                "provenance": {
                    **rogue["provenance"],
                    "section": "Character Classes: Rogue",
                },
            }
        )
        admin = catalog.upsert_entry(
            "srd-5.2.1-rogue.1",
            admin["ruleset"]["revision"],
            rogue,
        )
        feature = catalog.get_entry(
            "srd-5.2.1", "feature:second-wind"
        )["entry"]
        rogue_feature = {
            key: deepcopy(value)
            for key, value in feature.items()
            if key not in {"source", "license"}
        }
        rogue_feature.update(
            {
                "id": "feature:rogue-training",
                "slug": "rogue-training",
                "name": "Rogue Training",
                "data": {
                    **rogue_feature["data"],
                    "class_id": "class:rogue",
                    "effect": "Test-only open rules feature.",
                },
                "provenance": {
                    **rogue_feature["provenance"],
                    "section": "Character Classes: Rogue Training",
                },
            }
        )
        admin = catalog.upsert_entry(
            "srd-5.2.1-rogue.1",
            admin["ruleset"]["revision"],
            rogue_feature,
        )
        catalog.publish_ruleset(
            "srd-5.2.1-rogue.1",
            admin["ruleset"]["revision"],
            False,
        )

        character_engine = CharacterEngine(catalog)
        engine = CharacterDraftEngine(character_engine)
        character = character_engine.new_character(
            "rogue-1", "owner-1", "Shade", "srd-5.2.1-rogue.1"
        )
        draft = engine.new_creation_draft(character)
        draft = engine.patch(
            draft,
            {
                "class_id": "class:rogue",
                "skill_proficiencies": [
                    "arcana",
                    "insight",
                    "investigation",
                    "religion",
                    "stealth",
                ],
                "spellcasting": {
                    "ability": "wisdom",
                    "known_spell_ids": ["spell:cure-wounds"],
                    "prepared_spell_ids": ["spell:cure-wounds"],
                    "slots": {"1": 2},
                },
            },
        )
        engine.validate_step(
            draft, "proficiencies", "srd-5.2.1-rogue.1"
        )
        engine.validate_step(draft, "spells", "srd-5.2.1-rogue.1")
        built = engine.build_character(
            "rogue-1", "owner-1", "srd-5.2.1-rogue.1", draft
        )
        self.assertEqual(built["class_name"], "Rogue")
        self.assertEqual(built["resource_state"]["hit_dice"]["die_size"], 8)
        self.assertEqual(
            built["action_state"]["spellcasting"]["slots"]["1"]["maximum"], 2
        )

        invalid_slots = self.engine.patch(
            self.draft,
            {
                "class_id": "class:rogue",
                "spellcasting": {
                    "ability": "wisdom",
                    "known_spell_ids": ["spell:cure-wounds"],
                    "prepared_spell_ids": ["spell:cure-wounds"],
                    "slots": {"1": 4},
                },
            },
        )
        with self.assertRaisesRegex(
            CharacterDraftValidationError, "class ve level"
        ):
            engine.validate_step(
                invalid_slots, "spells", "srd-5.2.1-rogue.1"
            )


if __name__ == "__main__":
    unittest.main()
