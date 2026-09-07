"""Every command the server accepts should be reachable from the client.

This repository kept growing correct, well-tested mechanics that no player
could ever trigger: death saving throws had a faithful 5e implementation and
no button, and Short Rest sent a hard-coded zero Hit Dice so the healing path
was dead code. A rule that cannot be reached is not a shipped rule.

New server commands are therefore either wired into the UI or listed below
with the reason they are not.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SOURCE = ROOT / "web" / "src"

# Commands with no client caller, each with the reason it is acceptable.
SERVER_ONLY: dict[str, str] = {
    "update_character": "DM-side raw edit; the builder and engines own the aggregate.",
    "configure_character_actions": "Populated by the publish pipeline, not by hand.",
    "expend_resource": (
        "Generic fallback. Concrete resources ship their own control, "
        "for example use_second_wind."
    ),
    "set_encumbrance_policy": (
        "Campaign policy fixed at creation; no surface exposes changing it yet."
    ),
}


def accepted_commands() -> set[str]:
    source = (ROOT / "api" / "models.py").read_text(encoding="utf-8")
    match = re.search(
        r"class CommandRequest\(BaseModel\):\s*\n\s*type:\s*Literal\[(.*?)\]",
        source,
        re.S,
    )
    assert match, "CommandRequest.type literal not found"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def client_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(WEB_SOURCE.rglob("*.ts*"))
    )


class CommandReachabilityTest(unittest.TestCase):
    def test_every_command_is_reachable_or_declared_server_only(self):
        commands = accepted_commands()
        self.assertGreater(len(commands), 40, "command list looks truncated")
        client = client_text()
        unreachable = {
            command
            for command in commands
            if f'"{command}"' not in client
        }
        undeclared = sorted(unreachable - set(SERVER_ONLY))
        self.assertEqual(
            undeclared,
            [],
            "These commands exist on the server but nothing in the client can "
            "send them. Wire them up, or add them to SERVER_ONLY with a "
            f"reason: {undeclared}",
        )

    def test_the_server_only_list_does_not_go_stale(self):
        commands = accepted_commands()
        client = client_text()
        unknown = sorted(set(SERVER_ONLY) - commands)
        self.assertEqual(
            unknown, [], f"SERVER_ONLY names commands the server no longer has: {unknown}"
        )
        now_reachable = sorted(
            command for command in SERVER_ONLY if f'"{command}"' in client
        )
        self.assertEqual(
            now_reachable,
            [],
            "These are wired into the client now, so drop them from "
            f"SERVER_ONLY: {now_reachable}",
        )

    def test_short_rest_offers_the_hit_dice_the_engine_supports(self):
        # The engine rolls 1d(hit die) + Constitution per die spent. Sending a
        # constant zero made that unreachable, which is how it stayed hidden.
        client = client_text()
        self.assertNotIn(
            '"short_rest", { hit_dice: 0 }',
            client,
            "Short Rest must let the player choose how many Hit Dice to spend.",
        )


if __name__ == "__main__":
    unittest.main()
