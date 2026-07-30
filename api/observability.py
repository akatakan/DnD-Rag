from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from collections import Counter

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_TRACEPARENT = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)
_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


def correlation_headers(headers) -> tuple[str, str, str]:
    requested_id = headers.get("x-request-id", "")
    request_id = (
        requested_id
        if _REQUEST_ID.fullmatch(requested_id)
        else secrets.token_hex(16)
    )
    match = _TRACEPARENT.fullmatch(headers.get("traceparent", "").lower())
    trace_id = (
        match.group(1)
        if match
        and int(match.group(1), 16)
        and int(match.group(2), 16)
        else ""
    )
    if not trace_id:
        trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    return request_id, trace_id, f"00-{trace_id}-{span_id}-01"


class JsonFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "trace_id",
            "method",
            "route",
            "status",
            "duration_ms",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_json_logger(name: str = "tetsu.http") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(getattr(item, "_tetsu_json", False) for item in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._tetsu_json = True
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def content_security_policy(content_type: str) -> str:
    if content_type.lower().startswith("text/html"):
        return "; ".join(
            (
                "default-src 'self'",
                "base-uri 'none'",
                "object-src 'none'",
                "frame-ancestors 'none'",
                "form-action 'self'",
                "script-src 'self'",
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' blob: data:",
                "connect-src 'self'",
                "font-src 'self'",
                "media-src 'self' blob: data:",
                "worker-src 'self' blob:",
                "manifest-src 'self'",
            )
        )
    return "default-src 'none'"


class MetricsRegistry:
    """In-process HTTP telemetry with fixed labels and bounded memory."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: Counter[tuple[str, str, str]] = Counter()
        self._durations: Counter[tuple[str, str, float]] = Counter()
        self._duration_sum: Counter[tuple[str, str]] = Counter()
        self._active = 0

    def begin(self) -> float:
        with self._lock:
            self._active += 1
        return time.perf_counter()

    def finish(
        self,
        started: float,
        *,
        method: str,
        route: str,
        status: int,
    ) -> float:
        elapsed = max(0.0, time.perf_counter() - started)
        method = method.upper()
        if method not in _HTTP_METHODS:
            method = "OTHER"
        status_class = f"{status // 100}xx"
        with self._lock:
            self._active = max(0, self._active - 1)
            self._requests[(method, route, status_class)] += 1
            self._duration_sum[(method, route)] += elapsed
            for boundary in _BUCKETS:
                if elapsed <= boundary:
                    self._durations[(method, route, boundary)] += 1
        return elapsed

    def render(self) -> str:
        with self._lock:
            requests = dict(self._requests)
            durations = dict(self._durations)
            duration_sums = dict(self._duration_sum)
            active = self._active
        lines = [
            "# HELP tetsu_http_requests_total Completed HTTP requests.",
            "# TYPE tetsu_http_requests_total counter",
        ]
        for (method, route, status_class), value in sorted(requests.items()):
            labels = _labels(
                method=method, route=route, status_class=status_class
            )
            lines.append(f"tetsu_http_requests_total{{{labels}}} {value}")
        lines.extend(
            [
                "# HELP tetsu_http_request_duration_seconds HTTP latency.",
                "# TYPE tetsu_http_request_duration_seconds histogram",
            ]
        )
        route_pairs = sorted(duration_sums)
        for method, route in route_pairs:
            labels = _labels(method=method, route=route)
            for boundary in _BUCKETS:
                value = durations.get((method, route, boundary), 0)
                lines.append(
                    "tetsu_http_request_duration_seconds_bucket"
                    f'{{{labels},le="{boundary:g}"}} {value}'
                )
            count = sum(
                value
                for (m, r, _status), value in requests.items()
                if m == method and r == route
            )
            lines.append(
                "tetsu_http_request_duration_seconds_bucket"
                f'{{{labels},le="+Inf"}} {count}'
            )
            lines.append(
                f"tetsu_http_request_duration_seconds_sum{{{labels}}} "
                f"{duration_sums[(method, route)]:.9f}"
            )
            lines.append(
                f"tetsu_http_request_duration_seconds_count{{{labels}}} {count}"
            )
        lines.extend(
            [
                "# HELP tetsu_http_requests_active Active HTTP requests.",
                "# TYPE tetsu_http_requests_active gauge",
                f"tetsu_http_requests_active {active}",
            ]
        )
        return "\n".join(lines) + "\n"


def _labels(**values: str) -> str:
    return ",".join(
        f'{key}="{_escape(value)}"' for key, value in values.items()
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
