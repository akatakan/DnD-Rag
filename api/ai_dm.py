from dataclasses import dataclass

from api.models import AuthContext, CommandRequest
from api.store import GameStore
from dice import roll


@dataclass
class AIDMPlan:
    actions: list[dict]
    narration_intent: str
    rationale: str

    def to_dict(self) -> dict:
        return {
            "actions": self.actions,
            "narration_intent": self.narration_intent,
            "rationale": self.rationale,
        }


class AIDMOrchestrator:
    """Produces structured plans; the game engine remains authoritative."""

    def __init__(self, store: GameStore):
        self.store = store

    def plan(self, auth: AuthContext, objective: str) -> AIDMPlan:
        game = self.store.game(auth.game_id)
        state = game["state"]
        if state["encounter_status"] != "active" or not state["combatants"]:
            return AIDMPlan(
                actions=[],
                narration_intent="The scene waits for the party's next decision.",
                rationale="No active encounter is available for an automated turn.",
            )

        actor = state["combatants"][state["turn_index"]]
        players = [item for item in state["characters"].values() if item["hp"] > 0]
        if actor.get("kind") == "player" or not players:
            return AIDMPlan(
                actions=[{"type": "next_turn", "payload": {}}],
                narration_intent=f"{actor['name']} has the initiative.",
                rationale="AI DM does not choose actions for player characters.",
            )

        target = min(players, key=lambda item: item["hp"])
        attack = roll("1d20+3")
        actions = [
            {
                "type": "attack",
                "actor_id": actor["id"],
                "target_id": target["id"],
                "attack_total": attack.total,
                "target_ac": target["ac"],
                "hit": attack.total >= target["ac"],
            }
        ]
        if attack.total >= target["ac"]:
            damage = roll("1d6+1")
            actions.append(
                {
                    "type": "apply_damage",
                    "payload": {"character_id": target["id"], "amount": damage.total},
                }
            )
        actions.append({"type": "next_turn", "payload": {}})
        return AIDMPlan(
            actions=actions,
            narration_intent=f"{actor['name']} attacks {target['name']} as the encounter continues.",
            rationale=f"Objective: {objective}. Targeted the lowest-HP conscious character.",
        )

    @staticmethod
    def executable_commands(plan: AIDMPlan) -> list[CommandRequest]:
        return [
            CommandRequest(type=action["type"], payload=action.get("payload", {}))
            for action in plan.actions
            if action["type"] in {"apply_damage", "next_turn"}
        ]
