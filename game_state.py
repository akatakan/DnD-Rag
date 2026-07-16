from dataclasses import asdict, dataclass, field
from typing import Literal

EncounterStatus = Literal["idle", "active", "completed"]


@dataclass
class CharacterState:
    name: str = "Adventurer"
    character_class: str = "Fighter"
    level: int = 1
    armor_class: int = 10
    max_hp: int = 10
    current_hp: int = 10
    temporary_hp: int = 0
    inventory: list[str] = field(default_factory=list)

    def normalize(self) -> None:
        self.level = min(20, max(1, int(self.level)))
        self.armor_class = min(40, max(0, int(self.armor_class)))
        self.max_hp = max(1, int(self.max_hp))
        self.current_hp = min(self.max_hp, max(0, int(self.current_hp)))
        self.temporary_hp = max(0, int(self.temporary_hp))
        self.inventory = [item.strip() for item in self.inventory if item.strip()]

    def apply_damage(self, amount: int) -> tuple[int, int]:
        amount = max(0, int(amount))
        absorbed = min(self.temporary_hp, amount)
        self.temporary_hp -= absorbed
        hp_damage = amount - absorbed
        self.current_hp = max(0, self.current_hp - hp_damage)
        return absorbed, hp_damage

    def heal(self, amount: int) -> int:
        before = self.current_hp
        self.current_hp = min(self.max_hp, self.current_hp + max(0, int(amount)))
        return self.current_hp - before

    def summary(self) -> str:
        inventory = ", ".join(self.inventory) if self.inventory else "boş"
        return (
            f"Karakter: {self.name}, seviye {self.level} {self.character_class}; "
            f"AC {self.armor_class}; HP {self.current_hp}/{self.max_hp}; "
            f"geçici HP {self.temporary_hp}; envanter: {inventory}."
        )


@dataclass
class Combatant:
    name: str
    initiative: int
    hp: int | None = None


@dataclass
class EncounterState:
    status: EncounterStatus = "idle"
    round_number: int = 0
    turn_index: int = 0
    combatants: list[Combatant] = field(default_factory=list)

    @property
    def current(self) -> Combatant | None:
        if self.status != "active" or not self.combatants:
            return None
        return self.combatants[self.turn_index]

    def start(self) -> None:
        if not self.combatants:
            raise ValueError("Encounter başlatmak için en az bir katılımcı ekleyin.")
        self.combatants.sort(key=lambda item: item.initiative, reverse=True)
        self.status = "active"
        self.round_number = 1
        self.turn_index = 0

    def next_turn(self) -> Combatant:
        if self.status != "active" or not self.combatants:
            raise ValueError("Aktif encounter bulunamadı.")
        self.turn_index += 1
        if self.turn_index >= len(self.combatants):
            self.turn_index = 0
            self.round_number += 1
        return self.combatants[self.turn_index]

    def complete(self) -> None:
        self.status = "completed"

    def reset(self) -> None:
        self.status = "idle"
        self.round_number = 0
        self.turn_index = 0
        self.combatants = []

    def summary(self) -> str:
        if not self.combatants:
            return "Encounter yok."
        order = ", ".join(
            f"{item.name} ({item.initiative})" for item in self.combatants
        )
        current = self.current.name if self.current else "yok"
        return (
            f"Encounter {self.status}; tur {self.round_number}; "
            f"sıradaki {current}; initiative: {order}."
        )


@dataclass
class GameState:
    character: CharacterState = field(default_factory=CharacterState)
    encounter: EncounterState = field(default_factory=EncounterState)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict | None) -> "GameState":
        value = value or {}
        character = CharacterState(**value.get("character", {}))
        encounter_data = value.get("encounter", {})
        combatants = [Combatant(**item) for item in encounter_data.get("combatants", [])]
        encounter = EncounterState(
            status=encounter_data.get("status", "idle"),
            round_number=encounter_data.get("round_number", 0),
            turn_index=encounter_data.get("turn_index", 0),
            combatants=combatants,
        )
        character.normalize()
        if encounter.combatants:
            encounter.turn_index = min(encounter.turn_index, len(encounter.combatants) - 1)
        return cls(character=character, encounter=encounter, notes=value.get("notes", ""))

    def context(self) -> str:
        notes = self.notes.strip() or "Oturum notu yok."
        return f"{self.character.summary()}\n{self.encounter.summary()}\nOturum notları: {notes}"
