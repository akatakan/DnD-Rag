# AGENTS.md

## Purpose and Scope

This repository is a source-aware D&D rules assistant with two user surfaces:

1. `main.py` is the Streamlit RAG and single-session laboratory.
2. `api/` plus `web/` is the multiplayer application. FastAPI is authoritative;
   React renders role-specific views and sends commands.

Treat these as related but distinct applications. They share the RAG stack and dice
logic, but they currently use different state models and SQLite stores. Do not merge
the two state paths incidentally during a local change.

## Working Tree Safety

- Inspect `git status --short` before editing. Preserve unrelated user changes.
- Never read, print, commit, or overwrite `.env`; use `.env.example` for documentation.
- `runtime/`, `web/dist/`, test reports, caches, and virtual environments are generated.
  Do not hand-edit or commit them.
- Do not edit generated frontend output. Change `web/src/` and rebuild.
- The canonical frontend lockfile is `web/package-lock.json`; run npm commands from
  `web/`. A root-level npm lockfile is not part of the application build.

## Architecture

### RAG path

```text
PDFs + data/metadata.yaml
  -> ingestion.py
  -> one Qdrant collection per book (dense + BM25 sparse vectors)
  -> retriever.py metadata-filtered dense/hybrid retrieval
  -> router.py deterministic two-book routing or LLM fallback
  -> agent.py balanced/reranked nodes and sourced synthesis
  -> Streamlit or FastAPI rules endpoint
```

Important files:

- `config.py`: environment parsing, model/index defaults, chunking, prompts.
- `ingestion.py`: PDF hashing, metadata enrichment, signature-based re-indexing.
- `retriever.py`: Qdrant clients, index checks, metadata/page filters.
- `router.py`: canonical book routing and generic-catalog LLM fallback.
- `agent.py`: LLM selection, multi-book balancing, reranking, rules/story pipeline.
- `sources.py`: display-ready source extraction.
- `evaluate.py` and `evaluation/`: router and retrieval benchmarks.
- `errors.py`: user-safe service error normalization.

### Streamlit path

```text
main.py
  -> SessionStore(runtime/sessions.db)
  -> GameState dataclasses
  -> cached RAG engine
```

- `session_store.py` owns chat/session persistence.
- `game_state.py` owns the single-session character and encounter model.
- `dice.py` is shared deterministic parsing plus cryptographically random rolling.

### Multiplayer path

```text
React clients
  <-> REST + WebSocket
api/app.py
  -> GameEngine (authorization and command semantics)
  -> GameStore (SQLite campaigns, sessions, state, members, requests, events)
  -> ConnectionManager (local sockets + optional Redis coordination)
  -> AIDMOrchestrator (structured plans only)
```

- `api/models.py` defines request/authentication types.
- `api/app.py` is the HTTP/WebSocket composition root and performs snapshot redaction.
- `api/game_engine.py` is the only intended shared-state command authority.
- `api/character_engine.py` owns character schema v2 validation and derived-stat math.
- `api/resource_engine.py` owns resource schema v2, rests, class-resource use,
  death saves, concentration, and typed condition duration semantics.
- `api/inventory_engine.py` owns inventory schema v1, identity entries, currency,
  containers, equipment slots, attunement, and encumbrance calculations.
- `api/encounter_engine.py` owns encounter draft schema v1, combatant source/resource
  validation, and authoritative character-source hydration into live state.
- `api/migrations.py` owns the ordered multiplayer schema migration registry.
- `api/store.py` owns `runtime/multiplayer.db`.
- `api/db_admin.py` owns integrity-checked, non-overwriting SQLite backup/restore.
- `api/sqlite_scale_probe.py` is the bounded synthetic concurrency measurement tool;
  it is evidence for a decision, not a production capacity promise.
- `api/data_lifecycle.py` owns dry-run-first expired runtime retention.
- `api/observability.py` owns bounded HTTP metrics, JSON logs, and request/trace
  correlation. Never add raw paths, query strings, headers, bodies, or identity labels.
- `api/http_load_probe.py` is a bounded, localhost-first HTTP probe, not a production
  load generator.
- `api/release_gate.py` and `.github/workflows/release-gates.yml` define mandatory
  release verification and dependency audits.
- `docs/production-operations.md` is the TLS, monitoring, retention, scanning, load,
  and release runbook.
- `api/rate_limit.py` provides local fallback or shared Redis-backed sliding windows.
- `api/shared_runtime.py` owns Redis presence, pub/sub, recoverable grace scheduling,
  and the atomic shared rate-limit backend.
- `api/rules_catalog.py` validates and serves immutable open-rules catalog files.
- `api/security.py` validates public-mode pepper and HTTPS origin requirements.
- `api/realtime.py` owns live connections, role-aware broadcasts, and handover timers.
- `api/ai_dm.py` may propose commands; `GameEngine` still validates/applies them.
- `web/src/api.ts` is the client transport boundary.
- `web/src/types.ts` mirrors the server snapshot contract.

## Invariants

### Retrieval

Every indexed node must retain:

- `source_book`
- `source_book_id`
- `source_file`
- 1-based `page_number`
- `pdf_sha256`
- `index_version`
- `index_signature`

Collections are named `dnd_<pdf-stem>`. The signature includes index version, dense
model, sparse model, and PDF SHA-256. Changes to any of these require ingestion.

Dense retrieval is the production default. Hybrid improves MRR but currently lowers
page hit@10; local LLM reranking adds substantial latency. Do not change these defaults,
`HYBRID_ALPHA`, top-k values, chunking, routing terms, or prompts without rerunning the
relevant 40-question evaluation and recording the comparison.

Rules mode must make sourced claims from retrieved context. Story mode performs the
sourced rules pass first and must keep its sources and mechanics in the creative pass.
Memory/game context belongs in synthesis, not retrieval, unless deliberately redesigning
the retrieval contract.

### Multiplayer

- Clients request commands; clients never authoritatively mutate shared state.
- Mutation commands may carry `expected_revision`; stale revisions return HTTP 409.
  Retried commands must reuse the same `client_action_id`. Reusing that id with a
  different command payload is invalid.
- Authorization and visibility are enforced on the server, not only in React.
- Players can edit their own character profile/inventory, but HP changes go through
  requests and active-DM approval.
- Only the active DM may run authoritative DM commands.
- Ownership is permanent; active DM control may move to a co-DM and return.
- Campaign lifecycle and active-session lifecycle are separate. A session follows
  `preparing -> live -> paused/completed`, with `paused -> live/completed`; only the
  active DM may change it.
- Session notes, loot, quests, and summaries belong to the active session, not the
  campaign aggregate. Completed sessions are immutable except for the active DM's
  post-session summary.
- Session note visibility is stored as `party`, `dm_only`, or `player:<member_id>`.
  The client-facing `private` choice must be expanded to the authenticated member on
  the server; never persist or broadcast it as a global visibility value.
- Loot claims use a conditional `available -> claimed` update inside the command
  transaction. Do not implement claims as read-then-write or trust a client claimant.
- Draft summaries remain DM/co-DM only. Only a published summary is projected to
  players; unpublished summary content must not appear in party events.
- Encounter library records are campaign-scoped, versioned drafts. Only the active DM
  mutates them; DM/co-DM may read them. Updates use draft revision CAS in addition to
  the game aggregate revision/idempotency contract.
- Manual combatants persist explicit HP/max HP, AC and initiative. Character-sourced
  entries are references: starting the encounter must hydrate their current name, HP,
  AC and initiative from the authoritative character aggregate.
- Starting a saved encounter copies its validated roster into live state and records
  `active_encounter_id` plus its source revision. Never let a client directly author
  the live roster from an unvalidated draft.
- A live encounter is `active` or `paused` until completed. Pause preserves round,
  turn, roster and action ledger but blocks turn/gameplay mutations; resume must not
  reroll initiative or reset turn state.
- Live initiative ordering is deterministic: initiative descending, explicit
  `tie_breaker` descending, then case-folded name and stable ID. Reordering a tie or
  inserting a lair/environment entry must preserve the current actor by ID.
- Character-backed combatants are projections of the same character aggregate. Every
  command must synchronize combatant name, HP/max HP and AC before the state/event/
  revision transaction commits; never maintain a second authoritative HP pool.
- Lair and environment entries participate in turn order but have no HP/resources.
  Character action/resource commands must remain unavailable on those turns.
- Encounter undo history stores at most 20 pre-command state snapshots in SQLite.
  Snapshot push/pop, restored game state, revision, event and command receipt are one
  transaction. Corrupt history fails closed and is not consumed. Commands with
  non-state side effects such as request approval or draft publication are not
  state-only undo candidates.
- `human`, `assisted`, and `ai` are mutually exclusive game modes.
- Hidden combatants and monster HP must be removed before player snapshots are sent.
- Event visibility is `public`, `party`, `dm_only`, or `player:<member_id>`.
- WebSocket snapshots must rebuild fresh auth from persisted membership so role changes
  take effect without reconnecting.
- Persist only purpose-separated HMAC hashes for bearer tokens, invites, and WebSocket
  tickets. Raw secrets are returned once and must never enter snapshots or audit metadata.
- Public mode requires a private `AUTH_PEPPER`, explicit HTTPS origins, and one-time
  WebSocket tickets. Local mode may retain bearer-in-query fallback for LAN compatibility.
- Public traffic must arrive as HTTPS through a trusted proxy boundary. Do not trust
  client-supplied forwarded headers or weaken the public-mode 426 fail-closed behavior.
- Campaign export/delete is permanent-owner-only. Exports must remain one consistent
  snapshot with recursive credential redaction; deletion requires exact typed
  confirmation and must disconnect every affected game/member pair.
- Retention is dry-run by default. Never lower its 30-day command-receipt/credential
  floor or apply deletion without the exact confirmation and a verified backup.
- Production map uploads must retain structural validation and use fail-closed malware
  scanning. Content-addressed orphan cleanup stays offline to avoid same-digest races.
- `AUTH_PEPPER` is bound to each database by a fingerprint. Changing it is not an
  automatic rotation and must fail startup until credentials are deliberately reissued.
- Long-lived WebSockets must revalidate their bound auth-token row before broadcasts
  and on client messages; revocation or expiry removes access without reconnecting.
- AI plans are data. Dice rolls and mutations remain deterministic server operations.
- Character schema v2 keeps user selections under `inputs` and all calculations under
  `derived`. `ac`, `max_hp`, and `class_name` are read-only compatibility projections
  regenerated by `CharacterEngine`; never accept them as client-authored state.
- Ability modifiers use floor division, proficiency follows the level tiers, Fighter
  saves come from the pinned rules catalog, expertise requires skill proficiency, and
  each class-based level grants at least 1 HP after Constitution.
- Generic player `update_character` may change identity inputs, ability scores, and
  skill proficiency/expertise. Level is active-DM authoritative and monotonically
  increasing in this command; class changes require a future typed builder operation.
  It must not change AC, HP, or speed policies; those require future typed equipment,
  level-up, and rules operations.
- Player snapshots use a redacted `PublicCharacter`; do not type or treat another
  player's projection as a complete character aggregate.
- Resource mutations must run through `ResourceEngine` inside the same
  revision/idempotency transaction as the command. Do not let generic resource writes
  bypass typed feature effects such as Second Wind.
- Rest is invalid at 0 HP and during an active encounter. Normal healing cannot revive
  a character whose death-save status is `dead`; revival requires a future typed rule.
- Death saves are limited by monotonically increasing `turn_serial`, not round number.
  Starting an encounter initializes it to 1 and every `next_turn` increments it.
- Encounter action economy is persisted in `turn_actions` and reset on every encounter
  start/turn advance. Second Wind requires the character's active turn and consumes the
  turn's Bonus Action atomically with its resource use.
- Resource migrations validate current state, upgrade known older versions, and reject
  malformed or future schema versions without replacing persisted data.
- Inventory mutations use stable item IDs. Names are display values and the legacy
  `inventory` list is a read-only projection; never use a name as authoritative identity.
- Catalog-backed inventory fields are reconciled from the pinned rules catalog. Custom
  rule-bearing fields require active-DM authority. Equipment modifiers must be fed back
  through `CharacterEngine.recalculate` before state is persisted.
- Container graphs must remain acyclic, parent targets must be containers, capacity is
  checked against recursive content weight, and non-empty containers cannot be removed.
- Currency is non-negative integer CP/SP/EP/GP/PP and contributes one pound per 50
  coins. Standard carrying capacity is Strength × 15 lb; only active DM changes policy.
- Encounter equip/unequip requires the character's active turn and consumes `action`
  in the same `turn_actions` ledger used by other typed action-economy operations.
- Attunement is limited to three, disallows duplicate catalog item copies, runs with a
  Short Rest outside encounters, and ends on death.
- HTTP command responses must receive the same role-aware state redaction as snapshots;
  command receipts may never become a network path around hidden monster or other
  character private-sheet projections.
- Inventory and currency mutation events use `player:<owner_id>` visibility. The
  character owner and DM roles may inspect them; unrelated players must not receive
  item IDs, denominations, deltas, or rest details through events/catch-up.
- Action schema v1 is owned by `ActionEngine`. Only active-DM configuration may define
  spell access, slot maxima, or attack dice; players execute stable spell/attack IDs.
- Every rollable character action emits a typed intent with actor, source, action cost,
  mode, and normalized server-derived roll expression. Never accept a client-authored
  modifier, damage expression, target AC, or resolved total.
- Global custom rolls use `roll_intent`; the client may submit only the strict
  actor/action/visibility/context/dice shape. The server validates actor ownership,
  derives the expression and encounter context, resolves RNG, and appends
  `typed_roll_resolved`. Do not route the global FAB back through raw `roll`.
- Typed event metadata is schema v1 and game-scoped unique. Keep event append and
  command receipt in the same transaction so concurrent retries produce one Game Log
  entry. Private player rolls map to `player:<member_id>` and remain visible to DM roles.
- Keep a failed/lost Dice FAB request's payload-bound `client_action_id` for retry;
  allocating a new ID before an authoritative response creates a second RNG result.
  Player-visible roll contexts must not expose internal encounter IDs or raw turn
  indexes. Typed event columns, JSON payload, event type, actor membership, and schema
  version must remain coupled by migration/store validation.
- 3D dice is presentation only. Three/cannon must lazy-load after an authoritative
  result; never use visual body orientation, Math.random, or physics state as game RNG.
  The settled label must come from the server roll payload.
- Bound the 3D scene to 12 rendered dice, use the overflow indicator for the rest,
  throttle collision audio, and release RAF, bodies, geometries, materials, labels,
  canvas, and WebGL context after the presentation window. Preserve static output for
  reduced-motion and renderer-unavailable clients.
- Dice theme/sound preferences are member-scoped in migration v21. Validate the
  member/game pair in SQL, keep writes rate-limited and ordered client-side, and never
  advance shared game revision or broadcast a campaign snapshot for personal cosmetics.
- Preference reads must not overwrite a newer local edit. Coalesce rapid partial
  theme/sound patches against a current desired-value ref, cancel pending debounce on
  unmount, and keep read/write rate-limit buckets independent.
- Reduced-motion must select the static tray before importing the lazy Three/cannon
  module. Guard lazy import and WebGL init/context-loss failures with the same
  authoritative fallback; release oscillator/gain nodes and the shared AudioContext
  when DiceRoller unmounts.
- Spell-slot expenditure, attack damage/healing, turn-action consumption, event append,
  revision advance, and idempotency receipt belong to one `BEGIN IMMEDIATE` transaction.
  Preserve spent slots when maxima are reconfigured and restore them on Long Rest.
- Action migrations must reject non-object, malformed, and future versions rather than
  replacing them. Full action state is private sheet data; unrelated players receive
  only `PublicCharacter`.
- Character drafts live in `character_drafts`, not in shared `state_json`. Autosave
  advances the draft revision only; it must not advance or broadcast the game revision.
- New player joins create an active private draft and persist `character_ready=0` in
  the same transaction. Existing players migrate as ready only when they have a
  published draft; absent/active drafts remain incomplete. Until publish marks the
  member ready atomically, snapshots must force the builder and withhold the normal
  player workspace, campaign/session tools, and Dice FAB.
- Draft schema v2 accepts Standard Array only as the exact `15,14,13,12,10,8`
  multiset or Point Cost only as scores 8–15 totaling exactly 27 points. Background
  ability increases must follow the pinned catalog's allowed abilities and `+2/+1`
  or `+1/+1/+1` distribution. Do not add client-only or unproven random generation.
- Draft updates are strict top-level patches guarded by `expected_revision`. Forward
  navigation validates the current step, backward navigation is non-destructive, and
  publish always revalidates the complete draft against the pinned campaign catalog.
- Publishing builds a fresh authoritative aggregate through character, resource,
  inventory, and action engines. Draft status, game state, game revision, event, and
  command receipt must commit or roll back in the same transaction.
- Draft data is private to the character owner and active DM. Never include another
  player's draft in snapshots, public events, rule context, or WebSocket catch-up.
- The frontend builder serializes autosaves; never issue overlapping writes with the
  same draft revision. Preserve unsaved local fields while a save is in flight.
- A `409` draft conflict is explicit UI state. Do not silently reload, merge, or overwrite
  it; require the player to choose the server reload action. Navigation and publish stay
  blocked until the conflict/error state is resolved.
- Builder choices come from the pinned rules catalog and use stable IDs. Loading, empty,
  validation error, save error, conflict, published, mobile, keyboard, and reduced-motion
  states are part of the feature contract.
- Character-sheet ability, skill, save, and attack controls launch the shared dice dialog
  with a typed command descriptor. The displayed modifier is read-only and comes from the
  current character's derived state; the backend still creates and resolves the intent.
- Keep sheet tabs semantic (`tablist`, `tab`, `tabpanel`) and usable as horizontally
  scrolling navigation on narrow screens. Empty spells/resources and legacy optional
  action state must render without crashing the whole player workspace.
- Notes are explicitly device-local in CHAR-08. Cap them at 20,000 characters, namespace
  storage by game and character, and never imply server sync, sharing, or backup.
- Campaign settings schema v1 contains bounded house rules, safety tools, and Session Zero
  agenda. Active DM changes settings with `settings_version`; do not use last-write-wins.
- Each campaign member owns a monotonic `readiness_version`. `ready` requires accepted
  consent. Lines, veils, and private notes are visible only to that member and DM roles;
  party events contain status only, never preference content.
- Session scheduling changes only the active `preparing` session and is guarded by the
  shared game revision. Campaign/settings/member mutation plus revision and event must
  commit in one `BEGIN IMMEDIATE` transaction.
- Campaigns currently target `srd-5.2.1`. Bundled rules records must use the seven
  supported entity types and carry matching source, CC BY 4.0 license, attribution,
  document hash, page label, section, and curation provenance.
- `data/rulesets/<version>/catalog.json` is immutable source data, not runtime state.
  A new source document or corrected catalog is a new ruleset/catalog version; do not
  silently mutate a version already referenced by persisted characters or campaigns.
- Schema v1 pins the official SRD PDF URL and SHA-256, exact attribution, strict
  per-entity fields, canonical `type:slug` IDs, cross-references, and approved full-entry
  hashes (identity, name, data, source, license, and provenance). Expanding or correcting
  content requires an explicit schema/review change,
  not merely editing the JSON and relabeling it as SRD.
- D&D Beyond is only the official host for the open SRD source here. Do not copy its
  closed compendium, builder, character, or subscription content into the catalog.

When changing a command, update all of:

1. `CommandRequest` validation in `api/models.py`.
2. authorization, normalization, mutation, and event behavior in `api/game_engine.py`.
3. snapshot/event redaction if the new data can be secret.
4. `web/src/types.ts`, `web/src/api.ts`, and the relevant UI.
5. API tests and, for a visible workflow, Playwright coverage.

## Known Architectural Trade-offs and Hazards

These are current constraints, not guarantees to preserve forever. Address them
explicitly when a change touches the area.

- SQLite plus JSON state keeps the local MVP simple and portable. `GameEngine` commands
  use a reentrant `BEGIN IMMEDIATE` transaction so state, requests, and events commit
  atomically, but this intentionally serializes writers and limits throughput. Store
  connections use WAL, 10-second busy timeout, foreign keys, and `synchronous=FULL`.
  Redis coordination does not make SQLite multi-host safe.
- Do not overwrite a live database during restore. `api.db_admin` only creates a new
  target from one verified WAL snapshot and publishes it with no-overwrite semantics;
  verify it, stop the API, then switch `GAME_DB`. Preserve the old database for
  rollback. Never replace identity-unchecked paths in backup failure cleanup.
- PostgreSQL/outbox defer and trigger thresholds live in
  `docs/sqlite-postgresql-evaluation.md`. Multi-host deployment, measured lock/tail
  latency threshold breaches, PITR, or strict commit-to-client delivery require
  reopening that decision; do not claim the synthetic probe as a capacity benchmark.
- AI auto-apply commits the whole executable plan in one transaction and rejects a
  stale mode, active-DM, or `updated_at` snapshot before applying it.
- Events are an audit/feed record, not the source of truth. Snapshots return at most
  the latest 200 role-visible events. `/api/events` and WebSocket `catch_up` use the
  global event-id high-water cursor while preserving role visibility.
- With `REDIS_URL`, connection presence, pub/sub invalidation, disconnect propagation,
  grace deadlines, and rate limits are shared across workers. Redis is mandatory when
  `WEB_CONCURRENCY>1`; keep `INSTANCE_ID` as a human-readable worker label because a
  random per-process nonce is appended automatically. The subscriber and durable grace
  recovery start in FastAPI lifespan, not on the first socket. Keep synchronous Redis
  calls off the event loop, use Redis `TIME` in lease/window Lua scripts, and preserve
  per-game connect/heartbeat/disconnect serialization.
- Without `REDIS_URL`, ConnectionManager and RateLimiter intentionally remain
  process-local for a single-worker local/LAN server. Do not claim multi-worker
  correctness in that fallback mode.
- FastAPI async routes call synchronous SQLite and RAG/LLM work. Slow rule queries or
  heavy writes can block the event loop. Offload/cache deliberately before internet use.
- `/api/rules` rebuilds the RAG engine per request. This is isolated and simple but
  expensive; any cache must be keyed by provider and retrieval configuration.
- The SRD 5.2.1 catalog is intentionally a seven-record `foundation` seed, not an
  exhaustive rules corpus. `status` must remain `foundation` until completeness is
  measured and reviewed. Startup validates the bundled catalog before opening or
  migrating the database and fails if it is unavailable or invalid. Catalog reads use
  immutable process-local indexes and copy only returned data; deploying a changed file
  requires a process restart.
- Bearer tokens and invites expire, rotate, revoke, and are stored only as HMAC hashes;
  WebSockets use single-use tickets. Browser credentials still live in `localStorage`,
  so public deployments require strict XSS controls, TLS, a private pepper, and explicit
  HTTPS origins. Horizontal scaling requires the Redis shared runtime.
- Command payloads remain dictionaries with command-specific validation for the highest
  risk fields. Extend bounds, strings, IDs, visibility, and enum validation whenever a
  command grows; never trust TypeScript types.
- Request bodies are bounded before FastAPI/Pydantic parsing by
  `MAX_REQUEST_BODY_BYTES` (256 KiB default). Keep an equal or lower reverse-proxy cap;
  the application check covers both declared and streamed/chunked bodies.
- Character aggregates remain JSON inside the game state for atomic local-MVP writes.
  Migration v9 backfills legacy records and preserves unknown legacy class names, but
  the current catalog-backed default only models Fighter/Human/Acolyte and average
  Fighter HP. Equipment-driven AC, level-up choices, homebrew overrides, and normalized
  character tables belong to later backlog tasks.
- The React reconnect effect has an explicit disposed guard and validates stored
  credentials. Changes still need coverage for logout/unmount, invalid tokens, and
  reconnect cancellation.
- The RAG router is intentionally reliable for the two canonical book IDs but uses
  hard-coded vocabulary. Adding books requires routing and evaluation changes.
- Runtime index validation checks `index_version`, while ingestion uses the fuller
  `index_signature`. Model/PDF changes can therefore be detected late at query time.
- Re-ingestion deletes an old collection before the replacement finishes. A failed
  rebuild can temporarily remove a usable index; production-grade work should build
  and swap collections.
- Story-mode source/mechanics preservation is prompt-enforced, not mechanically proven.
- The frontend dependencies use broad `latest` ranges in `package.json`; rely on the
  committed lockfile (`npm ci`) and review lockfile changes intentionally.

## Setup and Commands

Baseline prerequisites are Python 3.10+, `uv`, and Node.js/npm. Ollama, its local
models, Docker/Qdrant, ingestion, and the Streamlit RAG surface are optional. Do not
make model downloads a prerequisite for the core multiplayer API or frontend.

```bash
uv sync
cd web
npm ci
npm run build
cd ..
uv run python run_api.py
```

For frontend development, run `npm run dev` in `web/` and the API separately. The
default development ports are 5173 and 8000.

Only when local rules/RAG or retrieval evaluation is wanted:

```bash
docker compose up -d
# Optional example only; users may choose any Ollama chat model.
ollama pull dolphin-llama3:8b
ollama pull nomic-embed-text
uv run python ingestion.py
uv run streamlit run main.py
```

The multiplayer game/session features remain usable without these optional services.
The `/api/rules` endpoint and rule drawer should report an unavailable-service error
until the RAG dependencies and indexes are present.
Never hard-code or automatically download a chat model. The user selects one through
`OLLAMA_LLM_MODEL`. `dolphin-llama3:8b` may be mentioned as an optional uncensored,
conversational RPG starting point, not as a required default. `nomic-embed-text` is a
separate embedding dependency for the current RAG pipeline.

## Validation

Use the smallest relevant check first, then broaden it. On Windows, if `uv run` cannot
access the global uv cache but `.venv` already exists, use
`.venv\Scripts\python.exe -m pytest` rather than changing project files.

```bash
# Complete Python suite
uv run python -m pytest -q

# Equivalent stdlib runner used by the project
uv run python -m unittest discover -s tests -v

# Frontend typecheck and production bundle
cd web
npm run build

# E2E: requires API on :8000, Vite on :5173, and the configured Edge channel
cd web
npm run test:e2e

# Retrieval/router evaluation; requires Qdrant, Ollama, and ingested PDFs
uv run python evaluate.py --mode router
uv run python evaluate.py --mode retrieval --retrieval dense
uv run python evaluate.py --mode retrieval --retrieval hybrid
```

Test expectations by change:

- Dice/state/store logic: focused unit test plus full Python suite.
- API command/auth/redaction: `tests/test_multiplayer_api.py` and full suite.
- DM presence/handover: `tests/test_dm_handover.py`, including async timing behavior.
- RAG routing/retrieval: unit tests plus the relevant evaluation report.
- React/type contract: frontend build; add/run Playwright for user-visible flows.
- Persistence/schema work: test both a new database and migration from the prior schema.
- Concurrency/transaction work: add a multi-connection regression test; ordinary API
  happy-path tests are not sufficient.
- VTT map work: keep binary files outside SQLite, validate the file structure and
  dimensions before storage, enforce campaign scope in SQL, and test unpublished
  player redaction plus content authorization.
- VTT token work: persist only spatial/ownership metadata. Derive token labels,
  initiative, HP, turn highlight, and hidden visibility from the authoritative
  combatant state; never maintain a second encounter aggregate for the map. Moving
  and removing tokens require token-revision CAS, and snapshots must read the game
  revision plus token revisions from one transaction.
- VTT fog work: never expose revealed-cell geometry or an unfogged source image to a
  player while fog is enabled. Keep fog edits revision-checked and DM-only, transient
  geometry TTL-bound, and permanent events free of fog/draw coordinates. Verify both
  the raster mask and the protected player map projection.

Report unavailable external-service checks separately. Do not claim retrieval or E2E
verification when Qdrant, Ollama, browsers, or live servers were not actually run.

## Change Style

- Prefer small, explicit boundaries over adding more behavior to `api/app.py`.
- Keep persistence in stores, command policy in engines, transport in app/API modules,
  and presentation in React.
- Use Pydantic models or explicit validators at network boundaries.
- Preserve server-side redaction whenever adding snapshot or event fields.
- Never put bearer tokens in map URLs. Fetch protected map content with the
  Authorization header and expose only a short-lived browser Blob URL.
- Add migrations for persisted schema changes; never assume users have an empty DB.
- Keep tests hermetic with temporary SQLite databases. Mock LLM/retriever behavior in
  unit tests; reserve live services for evaluation/integration checks.
- Update `README.md` and `docs/` when verified behavior, setup, security assumptions,
  or operator workflow changes.
