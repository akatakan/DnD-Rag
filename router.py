import re
from collections.abc import Sequence

from llama_index.core.base.base_selector import (
    BaseSelector,
    SelectorResult,
    SingleSelection,
)
from llama_index.core.prompts import PromptTemplate
from llama_index.core.schema import QueryBundle
from llama_index.core.tools import ToolMetadata

MULTI_MARKERS = (
    "compare",
    "together",
    "both",
    "birlikte",
    "iki kaynak",
    "karşılaştır",
    "player and monster",
    "oyuncu ve canavar",
)

DM_TERMS = (
    "monster",
    "canavar",
    "challenge rating",
    "legendary",
    "lair action",
    "stat block",
    "goblin",
    "orc",
    "owlbear",
    "skeleton",
    "zombie",
    "red dragon",
    "tremorsense",
    "recharge",
)

def is_multi_query(query: str) -> bool:
    lowered = query.lower()
    return (
        any(marker in lowered for marker in MULTI_MARKERS)
        or ("player" in lowered and "monster" in lowered)
        or ("oyuncu" in lowered and "canavar" in lowered)
    )


ROUTER_PROMPT = PromptTemplate(
    """You route D&D rules questions to books. Choose only the books required to
answer the question. Player rules, character creation, classes, equipment,
spells, ability checks, combat, conditions, resting, and death saves belong to
PlayerDnDBasicRules. Monster stat blocks, challenge rating, monster traits,
legendary actions, and lair actions belong to DMBasicRules. Select both only
when the question explicitly asks to compare or combine player and monster rules.

Books:
{context_list}

Question: {query_str}

Return exactly one line, with no explanation:
BOOKS: comma-separated book IDs"""
)


class RobustBookSelector(BaseSelector):
    """LLM selector with deterministic parsing and domain guards."""

    def __init__(self, llm, max_outputs: int = 2):
        self._llm = llm
        self._max_outputs = max_outputs
        self._prompt = ROUTER_PROMPT

    def _get_prompts(self):
        return {"prompt": self._prompt}

    def _update_prompts(self, prompts):
        if "prompt" in prompts:
            self._prompt = prompts["prompt"]

    @staticmethod
    def _choices_text(choices: Sequence[ToolMetadata]) -> str:
        return "\n".join(
            f"{index}. {choice.name}: {choice.description}"
            for index, choice in enumerate(choices, start=1)
        )

    def _parse(
        self,
        prediction: str,
        choices: Sequence[ToolMetadata],
        query: str,
    ) -> SelectorResult:
        selected = []
        for index, choice in enumerate(choices):
            if choice.name and choice.name.lower() in prediction.lower():
                selected.append(index)

        if not selected:
            numbers = {
                int(match) - 1
                for match in re.findall(
                    r"(?:^|[\s(])([1-9])(?:[).,\s]|$)", prediction
                )
            }
            selected = sorted(index for index in numbers if index < len(choices))

        fallback = self._domain_fallback(query, choices)
        lowered = query.lower()
        if is_multi_query(lowered):
            selected = fallback
        elif any(term in lowered for term in DM_TERMS) or len(selected) != 1:
            selected = fallback

        selected = list(dict.fromkeys(selected))[: self._max_outputs]
        return SelectorResult(
            selections=[
                SingleSelection(index=index, reason="D&D book scope match")
                for index in selected
            ]
        )

    @staticmethod
    def _domain_fallback(
        query: str, choices: Sequence[ToolMetadata]
    ) -> list[int]:
        lowered = query.lower()
        names = [choice.name for choice in choices]
        if is_multi_query(lowered):
            return [
                names.index(book_id)
                for book_id in ("PlayerDnDBasicRules", "DMBasicRules")
                if book_id in names
            ]
        target = (
            "DMBasicRules"
            if any(term in lowered for term in DM_TERMS)
            else "PlayerDnDBasicRules"
        )
        return [names.index(target)] if target in names else [0]

    def _select(
        self, choices: Sequence[ToolMetadata], query: QueryBundle
    ) -> SelectorResult:
        names = {choice.name for choice in choices}
        canonical = {"PlayerDnDBasicRules", "DMBasicRules"}
        if canonical.issubset(names):
            return self._parse("", choices, query.query_str)
        prediction = self._llm.predict(
            self._prompt,
            context_list=self._choices_text(choices),
            query_str=query.query_str,
        )
        return self._parse(prediction, choices, query.query_str)

    async def _aselect(
        self, choices: Sequence[ToolMetadata], query: QueryBundle
    ) -> SelectorResult:
        names = {choice.name for choice in choices}
        canonical = {"PlayerDnDBasicRules", "DMBasicRules"}
        if canonical.issubset(names):
            return self._parse("", choices, query.query_str)
        prediction = await self._llm.apredict(
            self._prompt,
            context_list=self._choices_text(choices),
            query_str=query.query_str,
        )
        return self._parse(prediction, choices, query.query_str)
