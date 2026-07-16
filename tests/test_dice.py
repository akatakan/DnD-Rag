import unittest
from unittest.mock import patch

from dice import DiceError, parse_roll, roll, roll_command


class DiceTest(unittest.TestCase):
    def test_parses_advantage_style_expression(self):
        self.assertEqual(parse_roll("2d20kh1+5"), (2, 20, "kh1", 5))

    @patch("dice.secrets.randbelow", side_effect=[3, 17])
    def test_keeps_highest_roll(self, _random):
        result = roll("2d20kh1+5")
        self.assertEqual(result.rolls, (4, 18))
        self.assertEqual(result.total, 23)

    def test_rejects_unbounded_rolls(self):
        with self.assertRaises(DiceError):
            parse_roll("101d20")

    def test_extracts_chat_command(self):
        self.assertEqual(roll_command("/roll 2d6+3"), "2d6+3")
        self.assertIsNone(roll_command("How does damage work?"))


if __name__ == "__main__":
    unittest.main()
