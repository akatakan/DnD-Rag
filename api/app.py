import math
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent import build_engine
from api.ai_dm import AIDMOrchestrator
from api.game_engine import CommandError, GameEngine
from api.models import (
    AIDMStepRequest,
    AuthContext,
    CommandRequest,
    CreateGameRequest,
    JoinGameRequest,
    RuleQuestionRequest,
)
from api.realtime import ConnectionManager
from api.store import GameStore
from sources import extract_sources

DB_PATH = Path(os.getenv("GAME_DB", "runtime/multiplayer.db"))
store = GameStore(DB_PATH)
game_engine = GameEngine(store)
ai_dm = AIDMOrchestrator(store)
connections = ConnectionManager(store, float(os.getenv("DM_GRACE_SECONDS", "60")))

app = FastAPI(title="D&D Multiplayer API", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("WEB_ORIGIN", "http://localhost:5173")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def auth_from_token(token: str) -> AuthContext:
    auth = store.authenticate(token)
    if auth is None:
        raise HTTPException(status_code=401, detail="Gecersiz oturum token'i.")
    return auth


def require_auth(authorization: str = Header(default="")) -> AuthContext:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token gerekli.")
    return auth_from_token(authorization.removeprefix("Bearer ").strip())


def snapshot(auth: AuthContext) -> dict:
    game = store.game(auth.game_id)
    state = game["state"]
    members = [
        {
            **member,
            "online": connections.is_online(auth.game_id, member["id"]),
            "is_owner": member["id"] == game["owner_id"],
            "is_active_dm": member["id"] == game["active_dm_id"],
        }
        for member in store.members(auth.game_id)
    ]
    if auth.role == "player":
        visible_combatants = []
        for item in state["combatants"]:
            if item.get("hidden"):
                continue
            redacted = dict(item)
            if item.get("kind") == "monster":
                redacted.pop("hp", None)
            visible_combatants.append(redacted)
        own_character = state["characters"].get(auth.character_id)
        public_characters = {
            character_id: {
                "id": character["id"], "name": character["name"],
                "hp": character["hp"], "max_hp": character["max_hp"],
                "conditions": character["conditions"],
                **({
                    "ac": character["ac"], "temp_hp": character["temp_hp"],
                    "inventory": character["inventory"], "class_name": character["class_name"],
                    "level": character["level"],
                } if character_id == auth.character_id else {}),
            }
            for character_id, character in state["characters"].items()
        }
        state = {**state, "combatants": visible_combatants, "characters": public_characters}
        pending = []
    else:
        own_character = None
        pending = store.pending_requests(auth.game_id)
    return {
        "game": {
            "id": game["id"], "name": game["name"],
            "invite_code": game["invite_code"] if auth.role in {"dm", "co_dm"} else None,
            "dm_mode": game["dm_mode"], "owner_id": game["owner_id"],
            "active_dm_id": game["active_dm_id"],
            "fallback_dm_mode": game["fallback_dm_mode"], "handover": game["handover"],
        },
        "me": auth.model_dump(),
        "members": members,
        "state": state,
        "own_character": own_character,
        "pending_requests": pending,
        "events": store.events(auth),
    }


async def handle_dm_grace_expired(game_id: str, offline_dm_id: str) -> None:
    game = store.game(game_id)
    co_dm = next(
        (member for member in store.members(game_id)
         if member["role"] == "co_dm" and connections.is_online(game_id, member["id"])),
        None,
    )
    if co_dm:
        handover = {
            "status": "offered", "offline_dm_id": offline_dm_id,
            "candidate_id": co_dm["id"],
        }
        store.set_handover(game_id, handover)
        event = store.add_event(game_id, "dm_handover_offered", offline_dm_id, "party", handover)
    elif game["fallback_dm_mode"] == "vote_ai":
        players = [member["id"] for member in store.members(game_id) if member["role"] == "player"]
        handover = {
            "status": "vote_ai", "offline_dm_id": offline_dm_id,
            "eligible_voters": players, "votes": [],
            "required": max(1, math.ceil(len(players) / 2)),
        }
        store.set_dm_mode(game_id, "assisted")
        store.set_handover(game_id, handover)
        event = store.add_event(game_id, "ai_takeover_vote_started", offline_dm_id, "party", {
            "required": handover["required"], "eligible_count": len(players),
        })
    else:
        handover = {"status": "assisted", "offline_dm_id": offline_dm_id}
        store.set_dm_mode(game_id, "assisted")
        store.set_handover(game_id, handover)
        event = store.add_event(game_id, "dm_fallback_assisted", offline_dm_id, "party", {})
    await connections.broadcast_event(event)
    await connections.broadcast_snapshot(game_id, snapshot)


connections.on_grace_expired = handle_dm_grace_expired


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/games")
def create_game(request: CreateGameRequest):
    return store.create_game(request.name, request.dm_name, request.dm_mode)


@app.post("/api/games/join")
def join_game(request: JoinGameRequest):
    try:
        return store.join_game(request.invite_code, request.player_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/snapshot")
def get_snapshot(auth: AuthContext = Depends(require_auth)):
    return snapshot(auth)


@app.post("/api/commands")
async def command(request: CommandRequest, auth: AuthContext = Depends(require_auth)):
    try:
        result = game_engine.apply(auth, request)
    except (CommandError, KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await connections.broadcast_event(result["event"])
    await connections.broadcast_snapshot(auth.game_id, snapshot)
    return result


@app.post("/api/rules")
def ask_rules(request: RuleQuestionRequest, auth: AuthContext = Depends(require_auth)):
    state = snapshot(auth)["state"]
    engine = build_engine("ollama", rerank_enabled=False)
    response = engine.query(
        request.question,
        game_context=f"Multiplayer game state: {state}",
        response_mode=request.mode,
    )
    return {"answer": str(response), "sources": extract_sources(response)}


@app.post("/api/ai-dm/step")
async def ai_dm_step(request: AIDMStepRequest, auth: AuthContext = Depends(require_auth)):
    game = store.game(auth.game_id)
    if game["dm_mode"] == "human":
        raise HTTPException(status_code=400, detail="Human DM modunda AI plani kapalidir.")
    is_active_dm = auth.role in {"dm", "co_dm"} and auth.member_id == game["active_dm_id"]
    if game["dm_mode"] != "ai" and not is_active_dm:
        raise HTTPException(status_code=403, detail="Assisted modda AI planini yalnizca aktif DM olusturabilir.")
    active_member = store.member(auth.game_id, game["active_dm_id"])
    execution_auth = AuthContext(
        game_id=auth.game_id, member_id=active_member["id"], role=active_member["role"],
        character_id=active_member["character_id"], is_owner=active_member["id"] == game["owner_id"],
    )
    plan = ai_dm.plan(execution_auth, request.objective)
    applied = []
    should_apply = game["dm_mode"] == "ai" or request.auto_apply
    if should_apply:
        for executable in ai_dm.executable_commands(plan):
            result = game_engine.apply(execution_auth, executable)
            applied.append(result["event"])
            await connections.broadcast_event(result["event"])
        await connections.broadcast_snapshot(auth.game_id, snapshot)
    else:
        event = store.add_event(auth.game_id, "ai_plan_created", auth.member_id, "dm_only", plan.to_dict())
        await connections.broadcast_event(event)
    return {"plan": plan.to_dict(), "applied": applied, "requires_approval": not should_apply}


@app.websocket("/ws/games/{game_id}")
async def game_socket(websocket: WebSocket, game_id: str, token: str):
    auth = store.authenticate(token)
    if auth is None or auth.game_id != game_id:
        await websocket.close(code=4401)
        return
    await connections.connect(websocket, auth)
    await websocket.send_json({"kind": "snapshot", "snapshot": snapshot(auth)})
    await connections.broadcast_snapshot(game_id, snapshot)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connections.disconnect(websocket, auth)
        await connections.broadcast_snapshot(game_id, snapshot)


WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
