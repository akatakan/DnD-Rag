"""The DM must be able to see what they are about to reveal.

Fog shipped drawn at full opacity on both sides, which left the DM choosing
reveals while looking at a black rectangle. The fix is a see-through overlay on
the DM's own map, and it is safe because the player's fog is rendered into the
image bytes the server sends them: nothing the DM's client does with its own
overlay can expose a hidden cell. That boundary is covered behaviourally by
test_map_fog.test_fog_cells_are_dm_only_and_player_gets_raster_mask, so what is
left to lock is the part no server test can see — that the DM's overlay is
still drawn see-through.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DMFogIsAPlanningSurfaceTest(unittest.TestCase):
    def test_the_dm_overlay_is_drawn_see_through(self):
        css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
        rule = re.search(r"\.map-fog-mask\.dm\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule, ".map-fog-mask.dm rule is gone")
        opacity = re.search(r"opacity:\s*([\d.]+)", rule.group(1))
        self.assertIsNotNone(opacity, "the DM fog overlay declares no opacity")
        value = float(opacity.group(1))
        self.assertTrue(
            0.2 <= value < 1.0,
            f"DM fog opacity {value} is either opaque again or too faint to read as fog",
        )


if __name__ == "__main__":
    unittest.main()
