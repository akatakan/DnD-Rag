"""End-to-end SRD rule enforcement over the HTTP API.

The draft endpoint is a scratchpad: it stores whatever it is given. The rules
are enforced by the step machine (`navigate`) and again by
`publish_character_draft`, so a rule check that only POSTs a draft proves
nothing. Every case here therefore drives the same path a client does —
patch, navigate to review, publish — and asserts the *specific* rule message,
so a rejection that happens for an unrelated reason fails the test instead of
looking like a pass.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.character_draft_engine import POINT_COSTS, STANDARD_ARRAY
from api.game_engine import GameEngine
from api.realtime import ConnectionManager
from api.store import GameStore

# Background (Acolyte) grants insight + religion; the three free picks are
# Fighter's 2 (athletics, perception) plus Human's Skillful 1 (arcana).
VALID_SKILLS = ["arcana", "athletics", "insight", "perception", "religion"]
BACKGROUND_SKILLS = ["insight", "religion"]


class SRDRuleEnforcementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        self.game = self.client.post(
            "/api/games",
            json={"name": "Rules", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.seat = 0

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def join(self):
        """Each case needs its own character, since a draft publishes once."""
        self.seat += 1
        api_app.rate_limiter.clear()
        joined = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.game["invite_code"],
                "player_name": f"Seat{self.seat}",
            },
        )
        self.assertEqual(joined.status_code, 200, joined.text)
        body = joined.json()
        return body["token"], body["character_id"]

    def attempt(self, patch):
        """Run patch -> navigate -> publish. Returns (published, message)."""
        token, character_id = self.join()
        headers = {"Authorization": f"Bearer {token}"}
        route = f"/api/characters/{character_id}/draft"

        current = self.client.get(route, headers=headers).json()
        saved = self.client.patch(
            route,
            headers=headers,
            json={"expected_revision": current["revision"], "patch": patch},
        )
        if saved.status_code != 200:
            return False, self.detail(saved)
        current = saved.json()

        while current["current_step"] != "review":
            stepped = self.client.post(
                route + "/navigate",
                headers=headers,
                json={
                    "expected_revision": current["revision"],
                    "direction": "next",
                },
            )
            if stepped.status_code != 200:
                return False, self.detail(stepped)
            current = stepped.json()

        published = self.client.post(
            "/api/commands",
            headers=headers,
            json={
                "type": "publish_character_draft",
                "payload": {
                    "character_id": character_id,
                    "draft_revision": current["revision"],
                },
                "client_action_id": f"publish-seat-{self.seat}",
            },
        )
        if published.status_code != 200:
            return False, self.detail(published)
        return True, published.json()

    @staticmethod
    def detail(response):
        body = response.json()
        detail = body.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("message", detail))
        return str(detail)

    def assertRejected(self, patch, expected_fragment, label):
        published, message = self.attempt(patch)
        self.assertFalse(published, f"{label}: kural disi karakter yayinlandi")
        self.assertIn(
            expected_fragment,
            message,
            f"{label}: dogru kuraldan reddedilmedi -> {message}",
        )

    # --- control -----------------------------------------------------------

    def test_a_rules_valid_character_publishes_with_correct_derived_stats(self):
        published, result = self.attempt({"skill_proficiencies": VALID_SKILLS})
        self.assertTrue(published, f"gecerli karakter reddedildi: {result}")

        character = result["own_character"]
        derived = character["derived"]
        # Str 15 Dex 14 Con 13 Int 8+2 Wis 10+1 Cha 12, Fighter 1, Human.
        self.assertEqual(
            derived["ability_modifiers"],
            {
                "strength": 2, "dexterity": 2, "constitution": 1,
                "intelligence": 0, "wisdom": 0, "charisma": 1,
            },
        )
        self.assertEqual(derived["proficiency_bonus"], 2)
        # Fighter d10, fixed HP: 10 + Con modifier.
        self.assertEqual(character["max_hp"], 11)
        # Unarmoured: 10 + Dex modifier.
        self.assertEqual(derived["armor_class"], 12)
        self.assertEqual(derived["initiative"], 2)
        # Fighter is proficient in Strength and Constitution saves only.
        self.assertEqual(derived["saving_throws"]["strength"], 4)
        self.assertEqual(derived["saving_throws"]["constitution"], 3)
        self.assertEqual(derived["saving_throws"]["dexterity"], 2)
        self.assertEqual(derived["saving_throws"]["wisdom"], 0)

    # --- catalog identity ---------------------------------------------------

    def test_class_species_and_background_must_exist_in_the_pinned_catalog(self):
        for field, label in (
            ("class_id", "class"),
            ("species_id", "species"),
            ("background_id", "background"),
        ):
            self.assertRejected(
                {"skill_proficiencies": VALID_SKILLS, field: f"{label}:nonexistent"},
                "katalogda bulunamadi",
                f"{field} katalog disi",
            )

    def test_an_id_from_another_namespace_is_refused(self):
        # The shape guard pins each field to its own ID prefix, so a catalog ID
        # cannot be smuggled across entity kinds before the type check runs.
        for field, foreign_id in (
            ("class_id", "item:shield"),
            ("species_id", "class:fighter"),
            ("background_id", "species:human"),
        ):
            self.assertRejected(
                {"skill_proficiencies": VALID_SKILLS, field: foreign_id},
                f"{field} gecersiz",
                f"{field} baska namespace",
            )

    # --- ability scores -----------------------------------------------------

    def test_standard_array_must_use_each_value_exactly_once(self):
        abilities = (
            "strength", "dexterity", "constitution",
            "intelligence", "wisdom", "charisma",
        )
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "ability_score_method": "standard_array",
                "ability_scores": dict.fromkeys(abilities, 18),
            },
            "Standard Array",
            "array disi skorlar",
        )
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "ability_score_method": "standard_array",
                # 15 used twice, 8 dropped.
                "ability_scores": dict(
                    zip(abilities, (15, 15, 14, 13, 12, 10), strict=True)
                ),
            },
            "Standard Array",
            "tekrar eden array degeri",
        )

    def test_point_cost_must_spend_exactly_27_points_within_8_to_15(self):
        abilities = (
            "strength", "dexterity", "constitution",
            "intelligence", "wisdom", "charisma",
        )
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "ability_score_method": "point_cost",
                "ability_scores": dict(
                    zip(abilities, (16, 8, 8, 8, 8, 8), strict=True)
                ),
            },
            "8 ile 15 arasinda",
            "point cost araligi disi",
        )
        overspent = dict(zip(abilities, (15, 15, 15, 15, 8, 8), strict=True))
        # Four 15s cost 36 of the 27-point budget; state that here so the case
        # documents why it is illegal rather than just asserting a rejection.
        self.assertEqual(sum(POINT_COSTS[v] for v in overspent.values()), 36)
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "ability_score_method": "point_cost",
                "ability_scores": overspent,
            },
            "27 puan",
            "point cost butce asimi",
        )

    def test_a_scoring_method_must_be_chosen(self):
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "ability_score_method": "legacy_manual",
            },
            "Standard Array veya Point Cost",
            "yontem secilmemis",
        )

    # --- background ability increases --------------------------------------

    def test_oversized_background_increases_fail_the_shape_guard(self):
        # First layer: each bonus must be 1 or 2 and the total may not exceed 3,
        # so these never reach the distribution rule.
        for increases, label in (
            ({"intelligence": 3, "wisdom": 3}, "+3/+3"),
            ({"intelligence": 2, "wisdom": 2}, "+2/+2"),
            ({"intelligence": 2, "wisdom": 1, "charisma": 1}, "toplam 4"),
        ):
            self.assertRejected(
                {
                    "skill_proficiencies": VALID_SKILLS,
                    "background_ability_increases": increases,
                },
                "Background ability artislari gecersiz",
                f"background {label}",
            )

    def test_background_increases_follow_the_2024_plus_two_plus_one_rule(self):
        # Second layer: within the allowed totals, only +2/+1 and +1/+1/+1 are
        # legal distributions.
        for increases, label in (
            ({"intelligence": 2}, "tek +2"),
            ({"intelligence": 1}, "tek +1"),
            ({"intelligence": 1, "wisdom": 1}, "+1/+1"),
        ):
            self.assertRejected(
                {
                    "skill_proficiencies": VALID_SKILLS,
                    "background_ability_increases": increases,
                },
                "+2/+1 veya +1/+1/+1",
                f"background {label}",
            )

    def test_background_increases_must_target_listed_abilities(self):
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "background_ability_increases": {"strength": 2, "dexterity": 1},
            },
            "katalog secenekleriyle uyusmuyor",
            "background listesi disi ability",
        )

    def test_the_plus_one_plus_one_plus_one_split_is_accepted(self):
        published, result = self.attempt(
            {
                "skill_proficiencies": VALID_SKILLS,
                "background_ability_increases": {
                    "intelligence": 1, "wisdom": 1, "charisma": 1,
                },
            }
        )
        self.assertTrue(published, f"+1/+1/+1 reddedildi: {result}")
        scores = result["own_character"]["inputs"]["ability_scores"]
        self.assertEqual(scores["intelligence"], 9)
        self.assertEqual(scores["wisdom"], 11)
        self.assertEqual(scores["charisma"], 13)

    # --- skill proficiencies ------------------------------------------------

    def test_exactly_three_choices_are_allowed_beyond_the_background(self):
        self.assertRejected(
            {"skill_proficiencies": BACKGROUND_SKILLS + ["athletics", "perception"]},
            "tam 3 secim",
            "2 serbest secim",
        )
        self.assertRejected(
            {
                "skill_proficiencies": BACKGROUND_SKILLS
                + ["athletics", "perception", "arcana", "stealth"]
            },
            "tam 3 secim",
            "4 serbest secim",
        )

    def test_background_skills_cannot_be_dropped(self):
        self.assertRejected(
            {"skill_proficiencies": ["athletics", "perception", "arcana"]},
            "Background skill proficiencies eksik",
            "background skill'leri atlanmis",
        )

    def test_two_of_the_free_picks_must_come_from_the_class_list(self):
        # Arcana, stealth and investigation are all outside Fighter's list.
        self.assertRejected(
            {
                "skill_proficiencies": BACKGROUND_SKILLS
                + ["arcana", "stealth", "investigation"]
            },
            "Class icin izin verilen listeden",
            "hicbiri class listesinde degil",
        )

    def test_expertise_requires_proficiency_in_the_same_skill(self):
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "skill_expertise": ["stealth"],
            },
            "Expertise yalniz proficient skill",
            "proficient olmayan skill'de expertise",
        )

    # --- equipment and spellcasting ----------------------------------------

    def test_equipment_must_reference_catalog_items(self):
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "equipment_catalog_ids": ["item:nonexistent"],
            },
            "Equipment katalog kaydi bulunamadi",
            "katalog disi ekipman",
        )
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "equipment_catalog_ids": ["class:fighter"],
            },
            "item olmayan kayit",
            "ekipman olarak class",
        )

    def test_a_level_one_fighter_cannot_take_spellcasting(self):
        self.assertRejected(
            {
                "skill_proficiencies": VALID_SKILLS,
                "spellcasting": {
                    "ability": "intelligence",
                    "known_spell_ids": [],
                    "prepared_spell_ids": [],
                    "slots": {"1": 2},
                },
            },
            "spellcasting kullanamaz",
            "fighter 1 spell slotu",
        )

    # --- the constants the rules rest on ------------------------------------

    def test_the_srd_scoring_constants_are_unchanged(self):
        self.assertEqual(STANDARD_ARRAY, (15, 14, 13, 12, 10, 8))
        self.assertEqual(
            POINT_COSTS, {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
        )


if __name__ == "__main__":
    unittest.main()
