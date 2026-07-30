from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _validate_target(target: str, allow_remote: bool) -> None:
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Target mutlak bir HTTP(S) URL olmali.")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Target credential veya fragment iceremez.")
    local_names = {"127.0.0.1", "::1", "localhost"}
    if not allow_remote and parsed.hostname.lower() not in local_names:
        raise ValueError(
            "Remote load testi icin --allow-remote acikca verilmelidir."
        )


def _request_once(
    target: str, timeout_seconds: float, token: str
) -> tuple[float, int]:
    headers = {"User-Agent": "tetsu-bounded-load-probe/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(target, headers=headers, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.build_opener(_NoRedirect).open(
            request, timeout=timeout_seconds
        ) as response:
            response.read(64 * 1024)
            status = response.status
    except urllib.error.HTTPError as error:
        error.read(64 * 1024)
        status = error.code
    except (OSError, TimeoutError):
        status = 0
    return (time.perf_counter() - started) * 1000, status


def run_probe(
    target: str,
    *,
    requests: int = 200,
    concurrency: int = 8,
    timeout_seconds: float = 5.0,
    token: str = "",
    allow_remote: bool = False,
) -> dict:
    _validate_target(target, allow_remote)
    if not 1 <= requests <= 10_000:
        raise ValueError("Requests 1..10000 olmali.")
    if not 1 <= concurrency <= min(64, requests):
        raise ValueError("Concurrency 1..min(64, requests) olmali.")
    if not 0.1 <= timeout_seconds <= 60:
        raise ValueError("Timeout 0.1..60 saniye olmali.")
    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        outcomes = list(
            pool.map(
                lambda _: _request_once(target, timeout_seconds, token),
                range(requests),
            )
        )
    wall_seconds = max(time.perf_counter() - wall_started, 1e-9)
    latencies = [latency for latency, _status in outcomes]
    statuses: dict[str, int] = {}
    failures = 0
    for _latency, status in outcomes:
        key = str(status) if status else "transport_error"
        statuses[key] = statuses.get(key, 0) + 1
        if status < 200 or status >= 300:
            failures += 1
    return {
        "target": urllib.parse.urlunsplit(
            urllib.parse.urlsplit(target)._replace(query="")
        ),
        "requests": requests,
        "concurrency": concurrency,
        "failures": failures,
        "error_rate": round(failures / requests, 6),
        "throughput_rps": round(requests / wall_seconds, 3),
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "p99": round(_percentile(latencies, 0.99), 3),
            "max": round(max(latencies), 3),
        },
        "statuses": statuses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded Tetsu HTTP load probe."
    )
    parser.add_argument("target")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=5)
    parser.add_argument(
        "--token-env",
        default="",
        help="Bearer tokeni bu environment variable'dan oku.",
    )
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=500)
    arguments = parser.parse_args()
    if arguments.token_env and not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", arguments.token_env
    ):
        parser.error("--token-env gecerli bir environment variable olmali.")
    if (
        not math.isfinite(arguments.max_error_rate)
        or not 0 <= arguments.max_error_rate <= 1
        or not math.isfinite(arguments.max_p95_ms)
        or arguments.max_p95_ms <= 0
    ):
        parser.error("Gate esikleri sonlu ve gecerli olmali.")
    token = (
        os.environ.get(arguments.token_env, "")
        if arguments.token_env
        else ""
    )
    result = run_probe(
        arguments.target,
        requests=arguments.requests,
        concurrency=arguments.concurrency,
        timeout_seconds=arguments.timeout_seconds,
        token=token,
        allow_remote=arguments.allow_remote,
    )
    print(json.dumps(result, sort_keys=True))
    return int(
        result["error_rate"] > arguments.max_error_rate
        or result["latency_ms"]["p95"] > arguments.max_p95_ms
    )


if __name__ == "__main__":
    raise SystemExit(main())
