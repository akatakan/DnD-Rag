from typing import Annotated, Literal

from pydantic import BaseModel, Field

DMMode = Literal["human", "assisted", "ai"]
Role = Literal["dm", "co_dm", "player"]


class CreateGameRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dm_name: Annotated[str, Field(min_length=1, max_length=40)]
    dm_mode: DMMode = "human"


class JoinGameRequest(BaseModel):
    invite_code: Annotated[str, Field(min_length=6, max_length=12)]
    player_name: Annotated[str, Field(min_length=1, max_length=40)]


class CommandRequest(BaseModel):
    type: Literal[
        "roll", "request_damage", "request_heal", "approve_request",
        "reject_request", "apply_damage", "apply_heal", "set_dm_mode",
        "add_combatant", "start_encounter", "next_turn", "complete_encounter",
        "update_character", "update_scene", "add_item", "remove_item",
        "assign_co_dm", "remove_co_dm", "set_fallback_mode",
        "accept_dm_handover", "vote_ai_takeover", "reclaim_dm_control",
    ]
    payload: dict = Field(default_factory=dict)


class RuleQuestionRequest(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    mode: Literal["rules", "story"] = "rules"


class AIDMStepRequest(BaseModel):
    objective: Annotated[str, Field(min_length=1, max_length=1000)] = "Continue the encounter"
    auto_apply: bool = False


class AuthContext(BaseModel):
    game_id: str
    member_id: str
    role: Role
    character_id: str | None = None
    is_owner: bool = False
