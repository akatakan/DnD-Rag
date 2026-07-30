import json
import unittest

from fastapi.testclient import TestClient

import api.app as api_app
from api.observability import (
    MetricsRegistry,
    content_security_policy,
    correlation_headers,
)


class ObservabilityTest(unittest.TestCase):
    def test_correlation_accepts_valid_context_and_rejects_injection(self):
        trace_id = "a" * 32
        request_id, returned_trace, response_parent = correlation_headers(
            {
                "x-request-id": "web-123",
                "traceparent": f"00-{trace_id}-{'b' * 16}-01",
            }
        )
        self.assertEqual(request_id, "web-123")
        self.assertEqual(returned_trace, trace_id)
        self.assertTrue(response_parent.startswith(f"00-{trace_id}-"))
        _, rejected_trace, _ = correlation_headers(
            {
                "traceparent": (
                    f"00-{trace_id}-{'0' * 16}-01"
                )
            }
        )
        self.assertNotEqual(rejected_trace, trace_id)

        generated, _, _ = correlation_headers(
            {"x-request-id": "bad\nlog-entry"}
        )
        self.assertNotIn("\n", generated)
        self.assertNotEqual(generated, "bad\nlog-entry")

    def test_metrics_use_route_templates_and_fixed_status_classes(self):
        registry = MetricsRegistry()
        started = registry.begin()
        registry.finish(
            started, method="GET", route="/api/games/{game_id}", status=404
        )
        rendered = registry.render()
        self.assertIn('route="/api/games/{game_id}"', rendered)
        self.assertIn('status_class="4xx"', rendered)
        self.assertNotIn("secret-game-id", rendered)
        started = registry.begin()
        registry.finish(
            started, method="ATTACK-123", route="/unmatched", status=400
        )
        rendered = registry.render()
        self.assertIn('method="OTHER"', rendered)
        self.assertNotIn("ATTACK-123", rendered)

    def test_metrics_endpoint_is_disabled_or_token_protected(self):
        previous = api_app.METRICS_TOKEN
        try:
            api_app.METRICS_TOKEN = ""
            with TestClient(api_app.app) as client:
                self.assertEqual(client.get("/api/metrics").status_code, 404)
            api_app.METRICS_TOKEN = "m" * 32
            with TestClient(api_app.app) as client:
                self.assertEqual(client.get("/api/metrics").status_code, 401)
                response = client.get(
                    "/api/metrics",
                    headers={"X-Metrics-Token": "m" * 32},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("tetsu_http_requests_total", response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
        finally:
            api_app.METRICS_TOKEN = previous

    def test_json_log_does_not_include_headers_or_query_values(self):
        request_id, trace_id, _ = correlation_headers({})
        event = {
            "request_id": request_id,
            "trace_id": trace_id,
            "route": "/api/snapshot",
        }
        encoded = json.dumps(event)
        self.assertNotIn("Authorization", encoded)
        self.assertNotIn("token=", encoded)

    def test_html_csp_allows_bundled_ui_but_api_remains_closed(self):
        html_policy = content_security_policy("text/html; charset=utf-8")
        self.assertIn("script-src 'self'", html_policy)
        self.assertIn("style-src 'self' 'unsafe-inline'", html_policy)
        self.assertIn("img-src 'self' blob: data:", html_policy)
        self.assertIn("connect-src 'self'", html_policy)
        self.assertIn("object-src 'none'", html_policy)
        self.assertEqual(
            content_security_policy("application/json"),
            "default-src 'none'",
        )

        with TestClient(api_app.app) as client:
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn(
                "script-src 'self'",
                page.headers["content-security-policy"],
            )
            health = client.get("/api/health")
            self.assertEqual(
                health.headers["content-security-policy"],
                "default-src 'none'",
            )


if __name__ == "__main__":
    unittest.main()
