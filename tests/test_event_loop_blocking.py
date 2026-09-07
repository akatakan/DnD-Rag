"""Guard the invariant that async request handlers never touch SQLite directly.

GameStore is synchronous and serializes writes with BEGIN IMMEDIATE and a 10s
busy timeout, so one blocking call inside an `async def` stalls every other
request and every open WebSocket for as long as the write contends. Handlers
must therefore hand blocking work to asyncio.to_thread. Because GameStore keeps
its connection in a threading.local, a multi-call transaction has to go into a
single closure so it stays on one thread.
"""

import ast
import unittest
from pathlib import Path

BLOCKING_ROOTS = {"store", "game_engine", "ai_dm"}
MODULES = ("api/app.py", "api/realtime.py")
ROOT = Path(__file__).resolve().parents[1]


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _blocking_calls(module: Path) -> list[str]:
    """Blocking calls lexically inside an async def but outside any nested def.

    A nested sync function is only ever reached through asyncio.to_thread, so
    its body is off the loop and is skipped here.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    findings: list[str] = []

    def walk(node: ast.AST, in_async: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                walk(child, True)
                continue
            if isinstance(child, ast.FunctionDef):
                # Runs in a worker thread via to_thread, not on the loop.
                walk(child, False)
                continue
            if (
                in_async
                and isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and _root_name(child.func) in BLOCKING_ROOTS
            ):
                findings.append(
                    f"{module.name}:{child.lineno} "
                    f"{ast.unparse(child.func)}(...)"
                )
            walk(child, in_async)

    walk(tree, False)
    return findings


class EventLoopBlockingTest(unittest.TestCase):
    def test_async_handlers_do_not_call_the_store_on_the_event_loop(self):
        findings: list[str] = []
        for name in MODULES:
            findings.extend(_blocking_calls(ROOT / name))
        self.assertEqual(
            findings,
            [],
            "Bu cagrilar event loop'u blokluyor; asyncio.to_thread kullanin "
            "(tek transaction tek closure icinde kalmali):\n"
            + "\n".join(findings),
        )

    def test_detector_catches_a_direct_call_and_ignores_a_threaded_one(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            probe = Path(temporary) / "probe.py"
            probe.write_text(
                "import asyncio\n"
                "async def bad():\n"
                "    return store.game('x')\n"
                "async def good():\n"
                "    def work():\n"
                "        return store.game('x')\n"
                "    return await asyncio.to_thread(work)\n"
                "async def also_good():\n"
                "    return await asyncio.to_thread(store.game, 'x')\n",
                encoding="utf-8",
            )
            findings = _blocking_calls(probe)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("probe.py:3", findings[0])


if __name__ == "__main__":
    unittest.main()
