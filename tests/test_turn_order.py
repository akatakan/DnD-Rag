"""One turn order, one rule -- the live table and a saved draft agree.

A saved encounter draft used to sort on initiative alone. Python's sort is
stable, so two combatants on the same count fell back to the order the DM
happened to add them to the draft in, while the live table resolved the same
tie by tie-breaker, then name, then id. Two doors, two orders, same table.

Both now go through `sort_turn_order`. These tests fail if a draft ever starts
in insertion order again, or if `GameEngine._sort_turn_order` grows a second
opinion.
"""

import unittest

from api.encounter_engine import EncounterEngine, sort_turn_order
from api.game_engine import GameEngine


def combatant(name, initiative, **extra):
    return {
        "id": f"combatant-{name.lower()}",
        "source": {"type": "manual", "id": None},
        "name": name,
        "kind": "monster",
        "initiative": initiative,
        "hp": 10,
        "max_hp": 10,
        "armor_class": 12,
        "hidden": False,
        **extra,
    }


class TurnOrderTest(unittest.TestCase):
    def test_a_saved_draft_breaks_initiative_ties_instead_of_keeping_insertion_order(
        self,
    ):
        # Zara is added first and Alma second; on a shared initiative of 15 the
        # draft must still start in the order the live table would produce.
        draft = {
            "schema_version": 1,
            "name": "Kopru Pususu",
            "description": "",
            "combatants": [
                combatant("Zara", 15),
                combatant("Alma", 15),
                combatant("Borg", 20),
            ],
        }

        order = EncounterEngine.hydrate(draft, characters={})

        self.assertEqual(
            [item["name"] for item in order], ["Borg", "Alma", "Zara"]
        )

    def test_the_tie_breaker_outranks_the_name(self):
        # The DM's explicit tie-breaker is the whole point of the field: it has
        # to beat the alphabetical fallback, not merely survive alongside it.
        order = [
            {"id": "a", "name": "Alma", "initiative": 15, "tie_breaker": 0},
            {"id": "z", "name": "Zara", "initiative": 15, "tie_breaker": 7},
        ]

        sort_turn_order(order)

        self.assertEqual([item["name"] for item in order], ["Zara", "Alma"])

    def test_the_game_engine_uses_the_shared_rule(self):
        # `_sort_turn_order` delegates; if it ever forks, the live table and a
        # saved draft start disagreeing again in exactly the old way.
        rows = [
            {"id": "b", "name": "Bara", "initiative": 12, "tie_breaker": 0},
            {"id": "a", "name": "Alma", "initiative": 12, "tie_breaker": 4},
            {"id": "c", "name": "Cyra", "initiative": 18, "tie_breaker": 0},
        ]
        expected = [dict(row) for row in rows]
        sort_turn_order(expected)

        GameEngine._sort_turn_order(rows)

        self.assertEqual(rows, expected)
        self.assertEqual(
            [row["name"] for row in rows], ["Cyra", "Alma", "Bara"]
        )


if __name__ == "__main__":
    unittest.main()
