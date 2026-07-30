# Production Operations and Release Gates

This runbook is the operational contract for a public Tetsu deployment. Local/LAN
development may omit Redis, ClamAV, and metrics, but that profile is not a public
release candidate.

## Deployment boundary and TLS

Terminate TLS 1.2+ at a maintained reverse proxy or load balancer and forward only to
the API's private interface. Set `PUBLIC_MODE=true`, a stable random `AUTH_PEPPER` of
at least 32 characters, and HTTPS-only `WEB_ORIGIN` values. The proxy must:

- redirect port 80 to HTTPS before it reaches the application;
- overwrite, rather than append, forwarded headers and only trust its own network;
- forward the effective HTTPS scheme so Uvicorn exposes `request.url.scheme=https`;
- cap normal bodies at `MAX_REQUEST_BODY_BYTES` and map bodies at
  `MAX_MAP_UPLOAD_BYTES`;
- use bounded connect/read/write/idle timeouts and disable proxy caching for
  authenticated API responses;
- support WebSocket upgrade without logging query strings or authorization headers.

Start Uvicorn with `--proxy-headers` and
`--forwarded-allow-ips=<trusted proxy IP/CIDR>`. Never use `*`: only the reverse
proxy may assert the effective HTTPS/WSS scheme.

Public-mode HTTP requests fail with 426. HTTPS responses include HSTS, CSP
`default-src 'none'`, frame denial, no-sniff and no-referrer headers. Keep the React
site's CSP separate because the API policy intentionally permits no document content.

## Data lifecycle

Only the permanent campaign owner can call `GET /api/campaign/export`. It returns one
consistent campaign snapshot, omits credential/configuration tables, strips sensitive
columns and recursively strips sensitive JSON keys. It is `no-store`, rate limited,
and fails at 25 MiB. Map binaries are not embedded; their storage paths are secret
implementation details.

`DELETE /api/campaign` requires the exact confirmation
`<campaign_id>:<campaign_name>`. The database cascade and response are atomic, all
known campaign members are disconnected, and credentials become invalid. Deletion is
irreversible, so take and verify an export and database backup first. Content-addressed
map objects are deliberately left for offline garbage collection: synchronous unlink
could race another campaign uploading the same digest.

Expired runtime cleanup is dry-run by default:

```powershell
.\.venv\Scripts\python.exe -m api.data_lifecycle
```

Apply only after reviewing row counts and a backup:

```powershell
.\.venv\Scripts\python.exe -m api.data_lifecycle --apply --confirm PURGE_EXPIRED_RUNTIME_DATA
```

Defaults retain revoked/expired credentials and command receipts for 30 days and
security audit events for 365 days. Deleting an old command receipt removes its replay
deduplication record; the 30-day floor is therefore a security invariant, not merely a
storage preference. Run cleanup during a quiet window and alert on unexpected count
spikes.

## Logs, metrics, and tracing

HTTP access logs are one-line JSON containing timestamp, level, message, request ID,
trace ID, method, route template, status and duration. Raw paths, query strings,
headers, bodies, bearer tokens, invite codes and player content are never logged.
Forward valid W3C `traceparent` and optional safe `X-Request-ID`; the API returns both
correlation headers. This is propagation/correlation tracing, not a full span exporter.

Set a random `METRICS_TOKEN` of at least 32 characters to enable `GET /api/metrics`.
The endpoint otherwise returns 404 and requires `X-Metrics-Token`. Scrape it only over
the private network. Metrics use fixed route templates, a bounded HTTP-method set and
status classes to avoid
campaign/member IDs and unbounded label cardinality. Alert at minimum on:

- readiness 503 or Redis coordination degradation;
- 5xx rate, 429 rate, active-request growth and p95/p99 latency;
- WebSocket reconnect/disconnect churn;
- backup, retention, ClamAV and dependency-scan failures.

In-process metrics are per worker. Prometheus must scrape every worker or the deployment
must aggregate them through its platform.

## Upload and dependency scanning

PNG/JPEG map uploads already pass byte, MIME, CRC/segment, dimension, pixel and quota
validation. For a public release also set `UPLOAD_SCAN_REQUIRED=true` and point
`CLAMAV_HOST`/`CLAMAV_PORT` at a private maintained ClamAV daemon. Bytes use the
INSTREAM protocol and never need a temporary file. Detection returns 422; scanner
outage or an ambiguous response fails closed with 503.

CI runs Python and npm dependency audits. Dependabot watches Python, npm and GitHub
Actions weekly. A vulnerability exception needs an owner, affected version/risk
assessment, compensating control and expiry date; do not silently lower audit severity.

Current exception: `PYSEC-2026-597` affects NLTK's archive downloader and has no fixed
release as of 2026-07-30. NLTK is transitive; Tetsu never invokes `nltk.download`, never
accepts an NLTK archive, and runtime services have no reason to write NLTK data.
The audit ignores only this advisory. Security owner must recheck it by 2026-08-30 and
remove the exception immediately when an upstream fix is available. This exception does
not cover any other NLTK advisory or use of its downloader. CI and the local release
gate fail closed after that date even if the ignore flag remains in configuration.

## Bounded load probe

Start with health, then repeat against an authenticated read endpoint using a disposable
test campaign:

```powershell
.\.venv\Scripts\python.exe -m api.http_load_probe http://localhost:8000/api/health --requests 500 --concurrency 8
```

The tool allows at most 10,000 requests and 64 workers, strips query strings from its
report, does not follow redirects, and refuses remote targets unless `--allow-remote`
is explicit. Supply credentials through `--token-env VARIABLE`, not command-line
values that leak through shell history or process listings. Never point it
at production without change approval. The default gate is error rate at most 1% and
p95 at most 500 ms. Record hardware, database, endpoint, payload, concurrency, result
and date; synthetic results are not capacity promises. Also retain the SCALE-02 SQLite
write p99 guardrail: profiles above 250 ms require PostgreSQL evaluation/cutover.

## Release decision

Run:

```powershell
.\.venv\Scripts\python.exe -m api.release_gate
```

The release is eligible only when the lock check, complete Python suite, clean frontend
install/build, Python audit, production npm audit and diff hygiene all pass. The
`--skip-network-scans` mode is a local smoke check and intentionally returns a
non-eligible result. CI mirrors these gates in
`.github/workflows/release-gates.yml`.

Before a production rollout, additionally record:

1. verified SQLite backup/restore drill or PostgreSQL cutover/rollback drill;
2. Redis failover and multi-worker realtime test;
3. retention dry-run and ClamAV EICAR test in an isolated non-production environment;
4. bounded load result for the target release profile;
5. browser checks for responsive UI, WebGL dice, reduced motion, map gestures and
   WebSocket reconnect;
6. TLS scan, secret/config review, monitoring dashboard and rollback owner.

Any failed, skipped, or unavailable mandatory gate blocks release. Docker/Redis,
ClamAV, real browser, TLS-edge and production-disk tests remain environmental manual
gates when those services are unavailable locally.
