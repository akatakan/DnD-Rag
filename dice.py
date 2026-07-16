import re
import secrets
from dataclasses import dataclass

MAX_DICE = 100
MAX_SIDES = 1000
ROLL_PATTERN = re.compile(
    r"^\s*(?P<count>\d{0,3})d(?P<sides>\d{1,4})"
    r"(?P<keep>kh1|kl1)?(?P<modifier>[+-]\d{1,5})?\s*$",
    re.IGNORECASE,
)


class DiceError(ValueError):
    pass


@dataclass(frozen=True)
class RollResult:
    expression: str
    rolls: tuple[int, ...]
    kept: tuple[int, ...]
    modifier: int
    total: int

    def format(self) -> str:
        rolls = ", ".join(str(value) for value in self.rolls)
        modifier = f" {self.modifier:+d}" if self.modifier else ""
        return f"🎲 `{self.expression}` → [{rolls}]{modifier} = **{self.total}**"


def parse_roll(expression: str) -> tuple[int, int, str | None, int]:
    match = ROLL_PATTERN.fullmatch(expression)
    if not match:
        raise DiceError("Geçersiz zar ifadesi. Örnek: `2d6+3`, `2d20kh1+5`.")
    count = int(match.group("count") or 1)
    sides = int(match.group("sides"))
    keep = match.group("keep")
    modifier = int(match.group("modifier") or 0)
    if not 1 <= count <= MAX_DICE:
        raise DiceError(f"Zar sayısı 1-{MAX_DICE} arasında olmalıdır.")
    if not 2 <= sides <= MAX_SIDES:
        raise DiceError(f"Zar yüzü 2-{MAX_SIDES} arasında olmalıdır.")
    return count, sides, keep.lower() if keep else None, modifier


def roll(expression: str) -> RollResult:
    count, sides, keep, modifier = parse_roll(expression)
    values = tuple(secrets.randbelow(sides) + 1 for _ in range(count))
    if keep == "kh1":
        kept = (max(values),)
    elif keep == "kl1":
        kept = (min(values),)
    else:
        kept = values
    normalized = f"{count}d{sides}{keep or ''}{modifier:+d}".removesuffix("+0")
    return RollResult(
        expression=normalized,
        rolls=values,
        kept=kept,
        modifier=modifier,
        total=sum(kept) + modifier,
    )


def roll_command(text: str) -> str | None:
    match = re.fullmatch(r"\s*/(?:roll|r)\s+(.+?)\s*", text, re.IGNORECASE)
    return match.group(1) if match else None
