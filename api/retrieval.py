"""The table's link to the retrieval stack, and the only place that knows it exists.

"Kurala sor" is built on llama-index and Qdrant, which in turn want Ollama and
a running vector store. None of that is needed to open a map, roll a die or
keep a character sheet, so a table must still start when it is absent. The
heavy imports therefore happen inside the calls, and their absence is an
answerable error instead of a failure to boot.

Keeping the boundary in one module also means the stack can later move behind
an HTTP call to a separate retrieval service without any caller changing: the
two functions below are the whole contract.

Measured: importing api.app costs 629 ms and 465 modules on its own, and the
retrieval stack adds 3.6 s and roughly 2,000 more. The table now pays none of
that at startup. The trade is that the first rules question of a process pays
it instead -- acceptable, because answering one already means waiting on a
local LLM for seconds.
"""

from __future__ import annotations


class RetrievalUnavailable(RuntimeError):
    """The retrieval stack is not installed or cannot be reached."""


def ask(
    question: str, *, game_context: str, response_mode: str
) -> tuple[str, list[dict[str, object]]]:
    """Answer a rules question. Returns the answer and its page citations."""
    try:
        from agent import build_engine
        from sources import extract_sources
    except ImportError as error:  # pragma: no cover - depends on install shape
        raise RetrievalUnavailable(
            "Kural motoru bu kurulumda yok. `uv sync` ile retrieval "
            "bagimliliklarini kurun."
        ) from error

    engine = build_engine("ollama", rerank_enabled=False)
    response = engine.query(
        question, game_context=game_context, response_mode=response_mode
    )
    return str(response), extract_sources(response)


async def aclose() -> None:
    """Release the shared Qdrant clients, if the stack was ever loaded."""
    try:
        from retriever import aclose_clients
    except ImportError:  # pragma: no cover - nothing was opened to close
        return
    await aclose_clients()


def is_available() -> bool:
    """Whether a rules question could be answered, without loading the stack.

    find_spec locates the module without executing it. Importing here instead
    would make the health endpoint pay the retrieval stack's 3.6 s import --
    measured, on the first call -- which is precisely the cost this module
    exists to defer, and health is polled far more often than rules are asked.
    """
    from importlib.util import find_spec

    try:
        return find_spec("agent") is not None
    except (ImportError, ValueError):
        return False


__all__ = ["RetrievalUnavailable", "aclose", "ask", "is_available"]
