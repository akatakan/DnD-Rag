import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
import secrets
from math import ceil
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agent import build_engine
from api.ai_dm import AIDMOrchestrator
from api.game_engine import CommandError, GameEngine, RevisionConflict
from api.map_assets import LocalMapObjectStore, MapAssetError, validate_map_image
from api.map_fog import render_fog_mask, render_fogged_map
from api.models import (
    AIDMStepRequest,
    AuthContext,
    CommandRequest,
    CreateGameRequest,
    CreateSessionRequest,
    DeleteCampaignRequest,
    JoinGameRequest,
    RotateInviteRequest,
    RuleQuestionRequest,
    SaveCharacterDraftRequest,
    NavigateCharacterDraftRequest,
    ScheduleSessionRequest,
    UpdateCampaignSettingsRequest,
    UpdateDicePreferencesRequest,
    UpdateSessionZeroMemberRequest,
    UpdateSessionStatusRequest,
)
from api.observability import (
    MetricsRegistry,
    configure_json_logger,
    content_security_policy,
    correlation_headers,
)
from api.character_draft_engine import (
    DRAFT_STEPS,
    CharacterDraftStorageError,
    CharacterDraftValidationError,
)
from api.encounter_engine import EncounterDraftConflict, EncounterStorageError
from api.rate_limit import RateLimiter
from api.realtime import ConnectionManager
from api.rules_catalog import CatalogValidationError, RulesCatalog
from api.security import LOCAL_AUTH_PEPPER, validate_public_security
from api.shared_runtime import (
    RedisRateLimitBackend,
    RedisRealtimeCoordinator,
)
from api.store import (
    GameStore,
    MapFogConflict,
    MapSceneConflict,
    MapTokenConflict,
)
from api.upload_scan import (
    MalwareDetected,
    UploadScanError,
    scan_with_clamav,
)
from sources import extract_sources

DB_PATH = Path(os.getenv("GAME_DB", "runtime/multiplayer.db"))
PUBLIC_MODE = os.getenv("PUBLIC_MODE", "").strip().lower() in {
    "1", "true", "yes", "on",
}
AUTH_PEPPER = os.getenv("AUTH_PEPPER", "").strip() or LOCAL_AUTH_PEPPER
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", str(24 * 30)))
INVITE_TTL_HOURS = int(os.getenv("INVITE_TTL_HOURS", str(24 * 7)))
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(256 * 1024)))
MAX_MAP_UPLOAD_BYTES = int(os.getenv("MAX_MAP_UPLOAD_BYTES", str(10 * 1024 * 1024)))
UPLOAD_SCAN_REQUIRED = os.getenv(
    "UPLOAD_SCAN_REQUIRED", ""
).strip().lower() in {"1", "true", "yes", "on"}
CLAMAV_HOST = os.getenv("CLAMAV_HOST", "127.0.0.1").strip()
CLAMAV_PORT = int(os.getenv("CLAMAV_PORT", "3310"))
AUTH_PEPPER_BIND_EXISTING = os.getenv(
    "AUTH_PEPPER_BIND_EXISTING", ""
).strip().lower() in {"1", "true", "yes", "on"}
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_NAMESPACE = os.getenv("REDIS_NAMESPACE", "dnd-table").strip()
WEB_CONCURRENCY = int(os.getenv("WEB_CONCURRENCY", "1"))
if not REDIS_NAMESPACE or len(REDIS_NAMESPACE) > 64:
    raise RuntimeError("REDIS_NAMESPACE 1-64 karakter olmali.")
if WEB_CONCURRENCY < 1:
    raise RuntimeError("WEB_CONCURRENCY pozitif olmali.")
if WEB_CONCURRENCY > 1 and not REDIS_URL:
    raise RuntimeError(
        "Birden fazla worker icin REDIS_URL zorunludur."
    )
if (
    TOKEN_TTL_HOURS <= 0
    or INVITE_TTL_HOURS <= 0
    or MAX_REQUEST_BODY_BYTES <= 0
    or MAX_MAP_UPLOAD_BYTES <= 0
):
    raise RuntimeError("Token ve davet TTL değerleri pozitif olmalıdır.")
if MAX_MAP_UPLOAD_BYTES > 10 * 1024 * 1024:
    raise RuntimeError(
        "MAX_MAP_UPLOAD_BYTES kalici schema limiti olan 10 MiB'i asamaz."
    )
web_origins = [
    origin.strip()
    for origin in os.getenv("WEB_ORIGIN", "http://localhost:5173").split(",")
    if origin.strip()
]
validate_public_security(PUBLIC_MODE, AUTH_PEPPER, web_origins)
# Validate immutable deployment assets before any database migration can commit.
ruleset_root = os.getenv("RULESET_ROOT", "").strip()
rules_catalog = RulesCatalog(Path(ruleset_root) if ruleset_root else None)
rules_catalog.load("srd-5.2.1")
store = GameStore(
    DB_PATH,
    auth_pepper=AUTH_PEPPER,
    token_ttl_hours=TOKEN_TTL_HOURS,
    invite_ttl_hours=INVITE_TTL_HOURS,
    allow_existing_pepper_bind=AUTH_PEPPER_BIND_EXISTING,
)
map_object_root = Path(
    os.getenv("MAP_OBJECT_ROOT", "runtime/map-assets")
)
map_object_store = LocalMapObjectStore(map_object_root)
map_fog_cache_root = Path(
    os.getenv("MAP_FOG_CACHE_ROOT", "runtime/map-fog-cache")
).resolve()
map_fog_cache_root.mkdir(parents=True, exist_ok=True)
game_engine = GameEngine(store)
ai_dm = AIDMOrchestrator(store)
instance_label = (
    os.getenv("INSTANCE_ID", "").strip()
    or "worker"
)
instance_id = f"{instance_label}:{secrets.token_urlsafe(18)}"
realtime_coordinator = (
    RedisRealtimeCoordinator(
        REDIS_URL, instance_id, namespace=REDIS_NAMESPACE
    )
    if REDIS_URL
    else None
)
rate_limit_backend = (
    RedisRateLimitBackend(REDIS_URL, namespace=REDIS_NAMESPACE)
    if REDIS_URL
    else None
)
connections = ConnectionManager(
    store,
    float(os.getenv("DM_GRACE_SECONDS", "60")),
    coordinator=realtime_coordinator,
)
rate_limiter = RateLimiter(rate_limit_backend)
metrics = MetricsRegistry()
http_logger = configure_json_logger()
METRICS_TOKEN = os.getenv("METRICS_TOKEN", "").strip()
if METRICS_TOKEN and len(METRICS_TOKEN) < 32:
    raise RuntimeError("METRICS_TOKEN en az 32 karakter olmali.")


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    try:
        await connections.start_shared()
        yield
    finally:
        try:
            await connections.close_async()
        finally:
            await asyncio.to_thread(rate_limiter.close)


app = FastAPI(
    title="D&D Multiplayer API",
    version="0.6.0",
    lifespan=app_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=web_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Filename"],
)


@app.middleware("http")
async def enforce_public_origin(request: Request, call_next):
    if PUBLIC_MODE and request.url.scheme != "https":
        return JSONResponse(
            status_code=426,
            content={"detail": "HTTPS gerekli."},
            headers={"Upgrade": "TLS/1.2"},
        )
    origin = request.headers.get("origin")
    if PUBLIC_MODE and origin and origin not in web_origins:
        return JSONResponse(
            status_code=403, content={"detail": "Origin izinli değil."}
        )
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.url.path == "/api/maps/assets":
            client_host = (
                request.client.host if request.client else "unknown"
            )
            retry_after = await rate_limit_check_async(
                f"map_upload_body:{client_host}", 20
            )
            if retry_after is not None:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Cok fazla yukleme istegi. "
                        "Lutfen daha sonra tekrar deneyin."
                    },
                    headers={
                        "Retry-After": str(max(1, ceil(retry_after)))
                    },
                )
        body_limit = (
            MAX_MAP_UPLOAD_BYTES
            if request.url.path == "/api/maps/assets"
            else MAX_REQUEST_BODY_BYTES
        )
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"detail": "Content-Length gecersiz."}
                )
            if declared_length < 0:
                return JSONResponse(
                    status_code=400, content={"detail": "Content-Length gecersiz."}
                )
            if declared_length > body_limit:
                return JSONResponse(
                    status_code=413, content={"detail": "Istek govdesi cok buyuk."}
                )
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > body_limit:
                return JSONResponse(
                    status_code=413, content={"detail": "Istek govdesi cok buyuk."}
                )
            body.extend(chunk)
        request._body = bytes(body)
    return await call_next(request)


@app.middleware("http")
async def observe_http(request: Request, call_next):
    request_id, trace_id, response_traceparent = correlation_headers(
        request.headers
    )
    request.state.request_id = request_id
    request.state.trace_id = trace_id
    started = metrics.begin()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        route = getattr(request.scope.get("route"), "path", "__unmatched__")
        elapsed = metrics.finish(
            started,
            method=request.method,
            route=route,
            status=status,
        )
        http_logger.exception(
            "http_request_failed",
            extra={
                "request_id": request_id,
                "trace_id": trace_id,
                "method": request.method,
                "route": route,
                "status": status,
                "duration_ms": round(elapsed * 1000, 3),
            },
        )
        raise
    route = getattr(request.scope.get("route"), "path", "__unmatched__")
    elapsed = metrics.finish(
        started,
        method=request.method,
        route=route,
        status=status,
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["traceparent"] = response_traceparent
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = content_security_policy(
        response.headers.get("content-type", "")
    )
    if PUBLIC_MODE:
        response.headers[
            "Strict-Transport-Security"
        ] = "max-age=31536000; includeSubDomains"
    http_logger.info(
        "http_request",
        extra={
            "request_id": request_id,
            "trace_id": trace_id,
            "method": request.method,
            "route": route,
            "status": status,
            "duration_ms": round(elapsed * 1000, 3),
        },
    )
    return response


def auth_from_token(token: str) -> AuthContext:
    auth = store.authenticate(token)
    if auth is None:
        raise HTTPException(status_code=401, detail="Gecersiz oturum token'i.")
    return auth


def require_auth(authorization: str = Header(default="")) -> AuthContext:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token gerekli.")
    return auth_from_token(authorization.removeprefix("Bearer ").strip())


def bearer_token(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token gerekli.")
    return authorization.removeprefix("Bearer ").strip()


def enforce_rate_limit(bucket: str, key: str, limit: int) -> None:
    retry_after = rate_limiter.check(f"{bucket}:{key}", limit)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Cok fazla istek. Lutfen daha sonra tekrar deneyin.",
            headers={"Retry-After": str(max(1, ceil(retry_after)))},
        )


async def enforce_rate_limit_async(
    bucket: str, key: str, limit: int
) -> None:
    retry_after = await rate_limit_check_async(
        f"{bucket}:{key}", limit
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Cok fazla istek. Lutfen daha sonra tekrar deneyin.",
            headers={"Retry-After": str(max(1, ceil(retry_after)))},
        )


async def rate_limit_check_async(
    key: str, limit: int
) -> float | None:
    async_check = getattr(rate_limiter, "check_async", None)
    if async_check is not None:
        return await async_check(key, limit)
    # Preserve test/deployment adapters that implement the original protocol
    # while still keeping a synchronous shared backend off the event loop.
    return await asyncio.to_thread(rate_limiter.check, key, limit)


def project_state(auth: AuthContext, source_state: dict) -> dict:
    state = deepcopy(source_state)
    if auth.role != "player":
        return state
    current_combatant_id = None
    turn_index = state.get("turn_index")
    if (
        state.get("encounter_status") in {"active", "paused"}
        and isinstance(turn_index, int)
        and 0 <= turn_index < len(state["combatants"])
    ):
        current_combatant_id = state["combatants"][turn_index].get("id")
    visible_combatants = []
    for item in state["combatants"]:
        if item.get("hidden"):
            continue
        redacted = dict(item)
        if item.get("kind") == "monster":
            redacted.pop("hp", None)
            redacted.pop("max_hp", None)
        visible_combatants.append(redacted)
    public_characters = {}
    for character_id, character in state["characters"].items():
        if character_id == auth.character_id:
            public_characters[character_id] = character
        else:
            public_characters[character_id] = {
                "id": character["id"],
                "name": character["name"],
                "hp": character["hp"],
                "max_hp": character["max_hp"],
                "conditions": character["conditions"],
            }
    visible_action_ids = {
        item["id"] for item in visible_combatants
    } | set(state["characters"])
    projected_turn_index = -1
    if current_combatant_id is not None:
        projected_turn_index = next(
            (
                index
                for index, item in enumerate(visible_combatants)
                if item.get("id") == current_combatant_id
            ),
            -1,
        )
    return {
        **state,
        # Encounter draft identity and optimistic-lock metadata are DM-only.
        "active_encounter_id": None,
        "active_encounter_revision": None,
        "combatants": visible_combatants,
        "turn_index": projected_turn_index,
        "characters": public_characters,
        "turn_actions": {
            actor_id: usage
            for actor_id, usage in state.get("turn_actions", {}).items()
            if actor_id in visible_action_ids
        },
    }


def snapshot(auth: AuthContext) -> dict:
    # Keep the global revision, authoritative encounter state, spatial token
    # revisions and visible event cursor from one SQLite snapshot. Without
    # this boundary a token move could commit between reads and hand a client
    # an old game revision paired with a new token revision.
    with store.read_transaction():
        return _snapshot(auth)


def _snapshot(auth: AuthContext) -> dict:
    game = store.game(auth.game_id)
    campaign = store.campaign_for_game(auth.game_id)
    active_session = store.active_session(auth.game_id)
    invite = (
        store.active_invite(auth.game_id)
        if auth.is_owner or auth.member_id == game["active_dm_id"]
        else None
    )
    state = project_state(auth, game["state"])
    state["encounter_undo_available"] = (
        auth.role in {"dm", "co_dm"}
        and auth.member_id == game["active_dm_id"]
        and store.encounter_undo_count(auth.game_id) > 0
    )
    online_member_ids = connections.online_member_ids(auth.game_id)
    members = [
        {
            **member,
            "online": member["id"] in online_member_ids,
            "is_owner": member["id"] == game["owner_id"],
            "is_active_dm": member["id"] == game["active_dm_id"],
        }
        for member in store.members(auth.game_id)
    ]
    if auth.role == "player":
        own_character = state["characters"].get(auth.character_id)
        pending = []
    else:
        own_character = None
        pending = store.pending_requests(auth.game_id)
    return {
        "revision": game["state_revision"],
        "event_cursor": store.event_cursor(auth.game_id),
        "game": {
            "id": game["id"], "name": game["name"],
            "invite_code": None,
            "invite": invite,
            "dm_mode": game["dm_mode"], "owner_id": game["owner_id"],
            "active_dm_id": game["active_dm_id"],
            "fallback_dm_mode": game["fallback_dm_mode"], "handover": game["handover"],
        },
        "campaign": {
            "id": campaign["id"], "name": campaign["name"],
            "status": campaign["status"],
            "ruleset_version": campaign["ruleset_version"],
            "language": campaign["language"],
            "play_style": campaign["play_style"],
            "public_notes": campaign["public_notes"],
            "settings_version": campaign["settings_version"],
        },
        "session": {
            "id": active_session["id"],
            "campaign_id": active_session["campaign_id"],
            "number": active_session["number"],
            "title": active_session["title"],
            "status": active_session["status"],
            "scheduled_at": active_session["scheduled_at"],
            "started_at": active_session["started_at"],
            "ended_at": active_session["ended_at"],
        },
        "me": auth.model_dump(
            exclude={"auth_token_id", "auth_expires_at"}
        ),
        "members": members,
        "state": state,
        "map_scene": store.map_scene(auth, game),
        "own_character": own_character,
        "pending_requests": pending,
        "events": store.events(auth),
        "lobby": store.campaign_lobby(auth),
    }


async def handle_dm_grace_expired(game_id: str, offline_dm_id: str) -> None:
    online_member_ids = await connections.online_member_ids_async(game_id)
    with store.transaction():
        game = store.game(game_id)
        handover = game.get("handover") or {}
        if (
            game["active_dm_id"] != offline_dm_id
            or handover.get("status") != "grace"
            or handover.get("offline_dm_id") != offline_dm_id
            or offline_dm_id in online_member_ids
        ):
            return
        co_dm = next(
            (member for member in store.members(game_id)
             if member["role"] == "co_dm"
             and member["id"] in online_member_ids),
            None,
        )
        if co_dm:
            handover = {
                "status": "offered", "offline_dm_id": offline_dm_id,
                "candidate_id": co_dm["id"],
            }
            store.set_handover(game_id, handover)
            event = store.add_event(
                game_id, "dm_handover_offered", offline_dm_id,
                "party", handover,
            )
        elif game["fallback_dm_mode"] == "vote_ai":
            players = [
                member["id"] for member in store.members(game_id)
                if member["role"] == "player"
            ]
            handover = {
                "status": "vote_ai", "offline_dm_id": offline_dm_id,
                "eligible_voters": players, "votes": [],
                "required": max(1, ceil(len(players) / 2)),
            }
            store.set_dm_mode(game_id, "assisted")
            store.set_handover(game_id, handover)
            event = store.add_event(
                game_id, "ai_takeover_vote_started", offline_dm_id,
                "party", {
                    "required": handover["required"],
                    "eligible_count": len(players),
                },
            )
        else:
            handover = {
                "status": "assisted",
                "offline_dm_id": offline_dm_id,
            }
            store.set_dm_mode(game_id, "assisted")
            store.set_handover(game_id, handover)
            event = store.add_event(
                game_id, "dm_fallback_assisted", offline_dm_id,
                "party", {},
            )
    await connections.broadcast_event(event)
    await connections.broadcast_snapshot(game_id, snapshot)


connections.on_grace_expired = handle_dm_grace_expired
connections.snapshot_factory = snapshot


@app.get("/api/metrics", include_in_schema=False)
def prometheus_metrics(
    request: Request,
    x_metrics_token: str = Header(default="", alias="X-Metrics-Token"),
):
    if not METRICS_TOKEN:
        raise HTTPException(status_code=404, detail="Not found.")
    enforce_rate_limit(
        "metrics",
        request.client.host if request.client else "unknown",
        30,
    )
    if not secrets.compare_digest(x_metrics_token, METRICS_TOKEN):
        raise HTTPException(status_code=401, detail="Metrics token gerekli.")
    return Response(
        content=metrics.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
def health():
    if (
        realtime_coordinator is not None
        and not realtime_coordinator.is_healthy()
    ):
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "coordination": "redis",
            },
        )
    return {
        "status": "ok",
        "coordination": "redis" if REDIS_URL else "process-local",
    }


@app.post("/api/games")
def create_game(request: CreateGameRequest, http_request: Request):
    enforce_rate_limit("create", http_request.client.host if http_request.client else "unknown", 20)
    return store.create_game(request.name, request.dm_name, request.dm_mode)


@app.post("/api/games/join")
def join_game(request: JoinGameRequest, http_request: Request):
    enforce_rate_limit("join", http_request.client.host if http_request.client else "unknown", 30)
    try:
        return store.join_game(request.invite_code, request.player_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/snapshot")
def get_snapshot(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("snapshot", auth.member_id, 120)
    try:
        return snapshot(auth)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/campaign/export")
async def export_campaign(auth: AuthContext = Depends(require_auth)):
    await enforce_rate_limit_async(
        "campaign_export", auth.member_id, 3
    )
    try:
        exported = await asyncio.to_thread(
            store.campaign_portable_export, auth
        )
        content = json.dumps(
            exported, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(content) > 25 * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail="Campaign export 25 MiB sinirini asti.",
            )
        return Response(
            content,
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="campaign-{auth.game_id}.json"'
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.delete("/api/campaign")
async def delete_campaign(
    request: DeleteCampaignRequest,
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async(
        "campaign_delete", auth.member_id, 3
    )
    try:
        result = await asyncio.to_thread(
            store.delete_owned_campaign,
            auth,
            request.confirmation,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    for member in result["member_connections"]:
        try:
            await connections.disconnect_member(
                member["game_id"],
                member["member_id"],
                code=4404,
                trigger_grace=False,
            )
        except Exception:
            # Deletion and credential revocation already committed. Remote
            # sockets also fail the periodic persisted-auth heartbeat.
            continue
    return {
        "deleted": True,
        "campaign_id": result["campaign_id"],
        # Content-addressed binaries are intentionally left for offline GC;
        # synchronous unlink could race a same-SHA upload in another campaign.
        "orphan_map_objects": len(result["orphan_storage_keys"]),
    }


@app.get("/api/me/dice-preferences")
def get_dice_preferences(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("dice_preferences_read", auth.member_id, 60)
    try:
        return store.dice_preferences(auth)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.patch("/api/me/dice-preferences")
def update_dice_preferences(
    request: UpdateDicePreferencesRequest,
    auth: AuthContext = Depends(require_auth),
):
    enforce_rate_limit("dice_preferences_write", auth.member_id, 30)
    try:
        return store.update_dice_preferences(
            auth, request.theme, request.sound_enabled
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/maps/assets", status_code=201)
async def upload_map_asset(
    request: Request,
    x_filename: str = Header(default="map"),
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("map_upload", auth.member_id, 10)
    try:
        if (
            not 1 <= len(x_filename) <= 160
            or any(ord(character) < 32 for character in x_filename)
        ):
            raise MapAssetError("Harita dosya adi gecersiz.")
        data = await request.body()
        declared_content_type = request.headers.get("content-type", "")

        def persist() -> dict:
            game_engine.require_active_dm(auth)
            metadata = validate_map_image(
                data,
                declared_content_type,
                MAX_MAP_UPLOAD_BYTES,
            )
            if UPLOAD_SCAN_REQUIRED:
                scan_with_clamav(
                    data, host=CLAMAV_HOST, port=CLAMAV_PORT
                )
            with store.transaction():
                asset = store.create_map_asset(
                    auth,
                    x_filename,
                    f"{metadata['sha256']}.{metadata['extension']}",
                    metadata,
                )
                # Do not write a new object until the serialized quota
                # reservation has succeeded. Object-store failures roll the
                # metadata row back.
                map_object_store.put(
                    data, metadata["sha256"], metadata["extension"]
                )
                return asset

        return await asyncio.to_thread(persist)
    except CommandError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except MalwareDetected as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except UploadScanError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (MapAssetError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/maps/assets")
def list_map_assets(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("map_read", auth.member_id, 120)
    if auth.role not in {"dm", "co_dm"}:
        raise HTTPException(status_code=403, detail="Map asset listesi yalnizca DM icindir.")
    return {"assets": store.map_assets(auth)}


@app.get("/api/maps/assets/{asset_id}/content")
def get_map_asset_content(
    asset_id: str, auth: AuthContext = Depends(require_auth)
):
    enforce_rate_limit("map_content", auth.member_id, 240)
    try:
        with store.read_transaction():
            asset = store.map_asset_content(auth, asset_id)
            path = map_object_store.path(asset["storage_key"])
            fog = asset.get("fog")
            fog_data = (
                store.map_fog_mask(auth)
                if fog and fog["enabled"]
                else None
            )
    except (KeyError, MapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    response_path = path
    media_type = asset["content_type"]
    cache_control = "private, max-age=3600, immutable"
    etag = path.stem
    if fog_data is not None:
        cache_scope = hashlib.sha256(
            auth.game_id.encode("utf-8")
        ).hexdigest()
        response_path = map_fog_cache_root / (
            f"{cache_scope}-{asset['sha256']}"
            f"-fog-{fog_data['revision']}"
            f"-scene-{fog_data['scene_revision']}"
            f"-grid-{fog_data['grid_size_px']}.png"
        )
        try:
            render_fogged_map(
                path,
                response_path,
                fog_data["grid_size_px"],
                fog_data["revealed_cells"],
            )
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503, detail="Fog harita projection olusturulamadi."
            ) from error
        media_type = "image/png"
        cache_control = "private, max-age=0, must-revalidate"
        etag = response_path.stem
    return FileResponse(
        response_path,
        media_type=media_type,
        headers={
            "Cache-Control": cache_control,
            "ETag": f'"{etag}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


@app.get("/api/maps/scene")
def get_map_scene(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("map_read", auth.member_id, 120)
    return store.map_scene(auth)


@app.get("/api/maps/fog-mask")
async def get_map_fog_mask(auth: AuthContext = Depends(require_auth)):
    await enforce_rate_limit_async("map_content", auth.member_id, 240)
    try:
        fog = store.map_fog_mask(auth)
        content = await asyncio.to_thread(
            render_fog_mask,
            fog["columns"],
            fog["rows"],
            fog["revealed_cells"],
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(
        content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=0, must-revalidate",
            "ETag": (
                '"fog-'
                + hashlib.sha256(
                    (
                        f"{auth.game_id}:{fog['asset_sha256']}:"
                        f"{fog['revision']}:{fog['scene_revision']}:"
                        f"{fog['grid_size_px']}"
                    ).encode("utf-8")
                ).hexdigest()
                + '"'
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


@app.post("/api/auth/rotate")
async def rotate_auth_token(
    authorization: str = Header(default=""),
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("auth_rotate", auth.member_id, 10)
    try:
        result = store.rotate_token(bearer_token(authorization))
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await connections.disconnect_member(
        auth.game_id, auth.member_id, trigger_grace=False
    )
    return result


@app.post("/api/auth/logout")
async def logout(
    authorization: str = Header(default=""),
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("logout", auth.member_id, 10)
    revoked = store.revoke_token(bearer_token(authorization))
    if revoked:
        await connections.disconnect_member(auth.game_id, auth.member_id)
    return {"revoked": revoked}


@app.post("/api/ws-ticket")
def websocket_ticket(
    authorization: str = Header(default=""),
    auth: AuthContext = Depends(require_auth),
):
    enforce_rate_limit("ws_ticket", auth.member_id, 30)
    return store.create_websocket_ticket(auth, bearer_token(authorization))


def require_invite_manager(auth: AuthContext) -> None:
    game = store.game(auth.game_id)
    if not auth.is_owner and game["active_dm_id"] != auth.member_id:
        raise HTTPException(
            status_code=403,
            detail="Davetleri yalnızca oyun sahibi veya aktif DM yönetebilir.",
        )


@app.post("/api/invites/rotate")
async def rotate_invite(
    request: RotateInviteRequest,
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("invite_manage", auth.member_id, 20)
    require_invite_manager(auth)
    result = store.rotate_invite(
        auth.game_id, auth.member_id, request.max_uses
    )
    await connections.broadcast_snapshot(auth.game_id, snapshot)
    return result


@app.post("/api/invites/revoke")
async def revoke_invites(auth: AuthContext = Depends(require_auth)):
    await enforce_rate_limit_async("invite_manage", auth.member_id, 20)
    require_invite_manager(auth)
    result = {"revoked": store.revoke_invites(auth.game_id, auth.member_id)}
    await connections.broadcast_snapshot(auth.game_id, snapshot)
    return result


@app.get("/api/security/audit")
def security_audit(
    limit: int = 100,
    auth: AuthContext = Depends(require_auth),
):
    if not auth.is_owner:
        raise HTTPException(
            status_code=403, detail="Güvenlik kaydını yalnızca oyun sahibi görebilir."
        )
    return {"events": store.security_audit(auth.game_id, limit)}


@app.get("/api/events")
def get_events(
    after: int = 0,
    limit: int = 100,
    auth: AuthContext = Depends(require_auth),
):
    enforce_rate_limit("events", auth.member_id, 120)
    return store.event_page(auth, after, limit)


@app.get("/api/rulesets")
def get_rulesets(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("catalog", auth.member_id, 120)
    try:
        return {"rulesets": rules_catalog.versions()}
    except (CatalogValidationError, KeyError) as error:
        raise HTTPException(
            status_code=503, detail="Ruleset katalogu kullanima hazir degil."
        ) from error


def authorize_character_draft(auth: AuthContext, character_id: str) -> dict:
    game = store.game(auth.game_id)
    character = game["state"]["characters"].get(character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Karakter bulunamadi.")
    if auth.role == "player":
        if auth.character_id != character_id:
            raise HTTPException(status_code=403, detail="Draft erisimi reddedildi.")
    else:
        try:
            game_engine.require_active_dm(auth)
        except CommandError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
    return character


@app.post("/api/characters/{character_id}/draft")
def create_character_draft(
    character_id: str, auth: AuthContext = Depends(require_auth)
):
    enforce_rate_limit("character_draft", auth.member_id, 120)
    character = authorize_character_draft(auth, character_id)
    try:
        with store.transaction():
            return store.create_character_draft(auth.game_id, character)
    except CharacterDraftStorageError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (CharacterDraftValidationError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/characters/{character_id}/draft")
def get_character_draft(
    character_id: str, auth: AuthContext = Depends(require_auth)
):
    enforce_rate_limit("character_draft", auth.member_id, 120)
    authorize_character_draft(auth, character_id)
    try:
        draft = store.character_draft(auth.game_id, character_id)
    except CharacterDraftStorageError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if draft is None:
        raise HTTPException(status_code=404, detail="Character draft bulunamadi.")
    return draft


@app.patch("/api/characters/{character_id}/draft")
def save_character_draft(
    character_id: str,
    request: SaveCharacterDraftRequest,
    auth: AuthContext = Depends(require_auth),
):
    enforce_rate_limit("character_draft", auth.member_id, 120)
    authorize_character_draft(auth, character_id)
    try:
        with store.transaction():
            draft = store.character_draft(auth.game_id, character_id)
            if draft is None:
                raise KeyError("Character draft bulunamadi.")
            if (
                draft["revision"] != request.expected_revision
                or draft["status"] != "active"
            ):
                raise ValueError(
                    "Draft revision conflict: expected "
                    f"{request.expected_revision}, actual {draft['revision']}; "
                    f"status {draft['status']}."
                )
            data = store.character_draft_engine.patch(draft["data"], request.patch)
            return store.update_character_draft(
                auth.game_id,
                character_id,
                request.expected_revision,
                data,
                draft["current_step"],
            )
    except CharacterDraftStorageError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        status = 409 if "revision conflict" in str(error).lower() else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.post("/api/characters/{character_id}/draft/navigate")
def navigate_character_draft(
    character_id: str,
    request: NavigateCharacterDraftRequest,
    auth: AuthContext = Depends(require_auth),
):
    enforce_rate_limit("character_draft", auth.member_id, 120)
    character = authorize_character_draft(auth, character_id)
    try:
        with store.transaction():
            draft = store.character_draft(auth.game_id, character_id)
            if draft is None:
                raise KeyError("Character draft bulunamadi.")
            if (
                draft["revision"] != request.expected_revision
                or draft["status"] != "active"
            ):
                raise ValueError(
                    "Draft revision conflict: expected "
                    f"{request.expected_revision}, actual {draft['revision']}; "
                    f"status {draft['status']}."
                )
            index = DRAFT_STEPS.index(draft["current_step"])
            if request.direction == "next":
                store.character_draft_engine.validate_step(
                    draft["data"],
                    draft["current_step"],
                    character["ruleset_version"],
                )
                target = min(len(DRAFT_STEPS) - 1, index + 1)
            else:
                target = max(0, index - 1)
            return store.update_character_draft(
                auth.game_id,
                character_id,
                request.expected_revision,
                draft["data"],
                DRAFT_STEPS[target],
            )
    except CharacterDraftStorageError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, CharacterDraftValidationError) as error:
        status = 409 if "revision conflict" in str(error).lower() else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.get("/api/rulesets/{version}/entries")
def get_ruleset_entries(
    version: str,
    type: str | None = Query(default=None, max_length=20),
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=50, ge=1, le=100),
    auth: AuthContext = Depends(require_auth),
):
    enforce_rate_limit("catalog", auth.member_id, 120)
    try:
        return rules_catalog.list_entries(
            version,
            entity_type=type,
            query=q,
            offset=offset,
            limit=limit,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CatalogValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/rulesets/{version}/entries/{entry_id}")
def get_ruleset_entry(
    version: str,
    entry_id: str,
    auth: AuthContext = Depends(require_auth),
):
    enforce_rate_limit("catalog", auth.member_id, 120)
    try:
        return rules_catalog.get_entry(version, entry_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CatalogValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/sessions")
async def create_session(
    request: CreateSessionRequest,
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("session", auth.member_id, 30)
    try:
        with store.transaction():
            game = game_engine.require_active_dm(auth)
            session = store.create_session(auth.game_id, request.title)
            revision = store.advance_revision(
                auth.game_id, game["state_revision"]
            )
            event = store.add_event(
                auth.game_id,
                "session_created",
                auth.member_id,
                "party",
                {
                    "session_id": session["id"],
                    "number": session["number"],
                    "title": session["title"],
                },
            )
            session["revision"] = revision
    except (CommandError, KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await connections.broadcast_event(event)
    await connections.broadcast_snapshot(auth.game_id, snapshot)
    return session


@app.get("/api/sessions/current/workspace")
def get_session_workspace(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("session", auth.member_id, 60)
    try:
        return store.session_workspace(auth)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/encounters")
def get_encounter_library(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("encounter", auth.member_id, 60)
    if auth.role not in {"dm", "co_dm"}:
        raise HTTPException(
            status_code=403,
            detail="Encounter library yalnizca DM ekibine aciktir.",
        )
    try:
        game = store.game(auth.game_id)
        return {
            "encounters": store.encounter_drafts(game["campaign_id"]),
            "revision": int(game["state_revision"]),
        }
    except EncounterStorageError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/campaigns")
def list_member_campaigns(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("campaign", auth.member_id, 120)
    campaign = store.campaign_for_game(auth.game_id)
    return {
        "campaigns": [{
            "id": campaign["id"],
            "name": campaign["name"],
            "status": campaign["status"],
            "ruleset_version": campaign["ruleset_version"],
            "game_id": auth.game_id,
            "selected": True,
        }]
    }


@app.get("/api/campaigns/current/lobby")
def get_campaign_lobby(auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("campaign", auth.member_id, 120)
    try:
        return store.campaign_lobby(auth)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.patch("/api/campaigns/current/settings")
async def update_campaign_settings(
    request: UpdateCampaignSettingsRequest,
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("campaign", auth.member_id, 30)
    try:
        with store.transaction():
            game = game_engine.require_active_dm(auth)
            settings = {
                "schema_version": 1,
                "house_rules": [
                    rule.model_dump() for rule in request.house_rules
                ],
                "safety_tools": list(dict.fromkeys(request.safety_tools)),
                "session_zero_agenda": request.session_zero_agenda,
            }
            campaign = store.update_campaign_settings(
                auth.game_id, request.expected_version, settings
            )
            revision = store.advance_revision(
                auth.game_id, int(game["state_revision"])
            )
            event = store.add_event(
                auth.game_id,
                "campaign_settings_updated",
                auth.member_id,
                "party",
                {
                    "settings_version": campaign["settings_version"],
                    "house_rule_count": len(settings["house_rules"]),
                    "safety_tools": settings["safety_tools"],
                },
            )
    except (CommandError, KeyError, ValueError) as error:
        status = 409 if "conflict" in str(error).lower() else 400
        raise HTTPException(status_code=status, detail=str(error)) from error
    await connections.broadcast_event(event)
    await connections.broadcast_snapshot(auth.game_id, snapshot)
    return {
        "campaign": campaign,
        "lobby": store.campaign_lobby(auth),
        "revision": revision,
    }


@app.patch("/api/campaigns/current/session-zero")
async def update_session_zero_member(
    request: UpdateSessionZeroMemberRequest,
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("campaign", auth.member_id, 60)
    try:
        with store.transaction():
            game = store.game(auth.game_id)
            member = store.update_session_zero_member(
                auth.game_id,
                auth.member_id,
                request.expected_version,
                request.readiness_status,
                request.consent_status,
                {
                    "lines": request.lines,
                    "veils": request.veils,
                    "notes": request.notes,
                },
            )
            revision = store.advance_revision(
                auth.game_id, int(game["state_revision"])
            )
            event = store.add_event(
                auth.game_id,
                "session_zero_member_updated",
                auth.member_id,
                "party",
                {
                    "member_id": auth.member_id,
                    "readiness_status": member["readiness_status"],
                    "consent_status": member["consent_status"],
                },
            )
    except (KeyError, ValueError) as error:
        status = 409 if "conflict" in str(error).lower() else 400
        raise HTTPException(status_code=status, detail=str(error)) from error
    await connections.broadcast_event(event)
    await connections.broadcast_snapshot(auth.game_id, snapshot)
    return {
        "member": member,
        "lobby": store.campaign_lobby(auth),
        "revision": revision,
    }


@app.patch("/api/sessions/schedule")
async def schedule_session(
    request: ScheduleSessionRequest,
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("session", auth.member_id, 30)
    try:
        with store.transaction():
            game = game_engine.require_active_dm(auth)
            if int(game["state_revision"]) != request.expected_revision:
                raise RevisionConflict(
                    request.expected_revision, int(game["state_revision"])
                )
            scheduled_at = (
                request.scheduled_at.isoformat()
                if request.scheduled_at is not None
                else None
            )
            session = store.schedule_active_session(
                auth.game_id, scheduled_at
            )
            revision = store.advance_revision(
                auth.game_id, int(game["state_revision"])
            )
            event = store.add_event(
                auth.game_id,
                "session_scheduled",
                auth.member_id,
                "party",
                {
                    "session_id": session["id"],
                    "scheduled_at": session["scheduled_at"],
                },
            )
    except RevisionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (CommandError, KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await connections.broadcast_event(event)
    await connections.broadcast_snapshot(auth.game_id, snapshot)
    return {"session": session, "revision": revision}


@app.post("/api/sessions/status")
async def update_session_status(
    request: UpdateSessionStatusRequest,
    auth: AuthContext = Depends(require_auth),
):
    await enforce_rate_limit_async("session", auth.member_id, 30)
    try:
        with store.transaction():
            game = game_engine.require_active_dm(auth)
            if int(game["state_revision"]) != request.expected_revision:
                raise RevisionConflict(
                    request.expected_revision, int(game["state_revision"])
                )
            session = store.set_session_status(auth.game_id, request.status)
            revision = store.advance_revision(
                auth.game_id, game["state_revision"]
            )
            event = store.add_event(
                auth.game_id,
                "session_status_changed",
                auth.member_id,
                "party",
                {"session_id": session["id"], "status": session["status"]},
            )
            session["revision"] = revision
    except RevisionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (CommandError, KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await connections.broadcast_event(event)
    await connections.broadcast_snapshot(auth.game_id, snapshot)
    return session


@app.post("/api/commands")
async def command(request: CommandRequest, auth: AuthContext = Depends(require_auth)):
    await enforce_rate_limit_async("command", auth.member_id, 120)
    if request.type in {"map_ping", "map_draw"}:
        await enforce_rate_limit_async("map_signal", auth.member_id, 30)
    elif request.type in {"set_map_fog", "paint_map_fog"}:
        await enforce_rate_limit_async(
            "map_fog_write", auth.member_id, 60
        )
    try:
        result = game_engine.apply(auth, request)
    except (CharacterDraftStorageError, EncounterStorageError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except EncounterDraftConflict as error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(error),
                "expected_revision": error.expected,
                "actual_revision": error.actual,
            },
        ) from error
    except RevisionConflict as error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(error),
                "expected_revision": error.expected,
                "actual_revision": error.actual,
            },
        ) from error
    except (MapSceneConflict, MapTokenConflict, MapFogConflict) as error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(error),
                "expected_revision": error.expected,
                "actual_revision": error.actual,
            },
        ) from error
    except (CommandError, KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not result["replayed"]:
        await connections.broadcast_event(result["event"])
        await connections.broadcast_snapshot(auth.game_id, snapshot)
    result["state"] = project_state(auth, result["state"])
    current_game = store.game(auth.game_id)
    result["state"]["encounter_undo_available"] = (
        auth.role in {"dm", "co_dm"}
        and auth.member_id == current_game["active_dm_id"]
        and store.encounter_undo_count(auth.game_id) > 0
    )
    result["own_character"] = (
        result["state"]["characters"].get(auth.character_id)
        if auth.role == "player"
        else None
    )
    return result


@app.post("/api/rules")
def ask_rules(request: RuleQuestionRequest, auth: AuthContext = Depends(require_auth)):
    enforce_rate_limit("rules", auth.member_id, 20)
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
    await enforce_rate_limit_async("ai_dm", auth.game_id, 10)
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
        # A plan is one logical action. If a later command fails, roll back all
        # earlier state changes and events instead of leaving a partial turn.
        with store.transaction():
            fresh_game = store.game(auth.game_id)
            if (
                fresh_game["dm_mode"] != game["dm_mode"]
                or fresh_game["active_dm_id"] != game["active_dm_id"]
                or fresh_game["updated_at"] != game["updated_at"]
            ):
                raise HTTPException(status_code=409, detail="Oyun durumu degisti; AI planini yeniden olusturun.")
            for executable in ai_dm.executable_commands(plan):
                result = game_engine.apply(execution_auth, executable)
                applied.append(result["event"])
        for event in applied:
            await connections.broadcast_event(event)
        await connections.broadcast_snapshot(auth.game_id, snapshot)
    else:
        event = store.add_event(auth.game_id, "ai_plan_created", auth.member_id, "dm_only", plan.to_dict())
        await connections.broadcast_event(event)
    return {"plan": plan.to_dict(), "applied": applied, "requires_approval": not should_apply}


@app.websocket("/ws/games/{game_id}")
async def game_socket(
    websocket: WebSocket,
    game_id: str,
    ticket: str = "",
    token: str = "",
    after: int = 0,
):
    if PUBLIC_MODE and websocket.url.scheme != "wss":
        await websocket.close(code=4403)
        return
    origin = websocket.headers.get("origin")
    if PUBLIC_MODE and origin not in web_origins:
        await websocket.close(code=4403)
        return
    auth = (
        store.consume_websocket_ticket(ticket, game_id)
        if ticket
        else None if PUBLIC_MODE else store.authenticate(token)
    )
    if auth is None or auth.game_id != game_id:
        await websocket.close(code=4401)
        return
    if (
        await rate_limit_check_async(
            f"websocket:{auth.member_id}", 20
        )
        is not None
    ):
        await websocket.close(code=4429)
        return
    await connections.connect(websocket, auth)
    fresh_auth = store.refresh_auth_context(auth)
    if fresh_auth is None:
        await websocket.close(code=4401)
        await connections.disconnect_async(websocket, auth)
        return
    auth = fresh_auth
    await websocket.send_json({
        "kind": "catch_up",
        **store.event_page(auth, after, 200),
    })
    initial_snapshot = await asyncio.to_thread(snapshot, auth)
    await websocket.send_json({
        "kind": "snapshot",
        "snapshot": initial_snapshot,
    })
    await connections.broadcast_snapshot(game_id, snapshot)
    try:
        while True:
            try:
                await asyncio.wait_for(
                    websocket.receive_text(), timeout=30
                )
            except asyncio.TimeoutError:
                pass
            if not store.auth_context_active(auth):
                await websocket.close(code=4401)
                await connections.disconnect_async(
                    websocket, auth, trigger_grace=True
                )
                return
            await connections.heartbeat(websocket, auth)
    except WebSocketDisconnect:
        await connections.disconnect_async(websocket, auth)
        await connections.broadcast_snapshot(game_id, snapshot)


WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
