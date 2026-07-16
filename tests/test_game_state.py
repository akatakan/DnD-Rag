import unittest

from game_state import CharacterState, Combatant, EncounterState, GameState


class CharacterStateTest(unittest.TestCase):
    def test_damage_consumes_temporary_hp_first(self):
        character = CharacterState(max_hp=20, current_hp=20, temporary_hp=5)
        absorbed, hp_damage = character.apply_damage(8)
        self.assertEqual((absorbed, hp_damage), (5, 3))
        self.assertEqual((character.current_hp, character.temporary_hp), (17, 0))

    def test_healing_stops_at_max_hp(self):
        character = CharacterState(max_hp=20, current_hp=18)
        self.assertEqual(character.heal(10), 2)
        self.assertEqual(character.current_hp, 20)


class EncounterStateTest(unittest.TestCase):
    def test_sorts_initiative_and_advances_round(self):
        encounter = EncounterState(
            combatants=[Combatant("Goblin", 12), Combatant("Hero", 18)]
        )
        encounter.start()
        self.assertEqual(encounter.current.name, "Hero")
        encounter.next_turn()
        self.assertEqual(encounter.current.name, "Goblin")
        encounter.next_turn()
        self.assertEqual((encounter.current.name, encounter.round_number), ("Hero", 2))

    def test_round_trips_game_state(self):
        state = GameState()
        state.character.inventory = ["Rope"]
        state.encounter.combatants = [Combatant("Orc", 11, 15)]
        restored = GameState.from_dict(state.to_dict())
        self.assertEqual(restored.character.inventory, ["Rope"])
        self.assertEqual(restored.encounter.combatants[0].hp, 15)


if __name__ == "__main__":
    unittest.main()
