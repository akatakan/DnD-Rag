import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from api.http_load_probe import run_probe
from api.release_gate import (
    release_gates,
    run_gates,
    dependency_exceptions_current,
    workflow_actions_are_pinned,
)
from api.upload_scan import MalwareDetected, UploadScanError, scan_with_clamav


class ReleaseToolsTest(unittest.TestCase):
    def test_load_probe_is_bounded_and_remote_is_explicit(self):
        with self.assertRaises(ValueError):
            run_probe("https://example.com/health", requests=1)
        with self.assertRaises(ValueError):
            run_probe("http://localhost/health", requests=10_001)
        with patch(
            "api.http_load_probe._request_once",
            side_effect=[(10.0, 200), (30.0, 503)],
        ):
            result = run_probe(
                "http://localhost:8000/api/health?secret=hidden",
                requests=2,
                concurrency=1,
            )
        self.assertEqual(result["failures"], 1)
        self.assertEqual(result["latency_ms"]["p50"], 10.0)
        self.assertNotIn("secret", result["target"])
        with patch(
            "api.http_load_probe._request_once",
            return_value=(5.0, 302),
        ):
            redirected = run_probe(
                "http://localhost:8000/api/health",
                requests=1,
                concurrency=1,
            )
        self.assertEqual(redirected["failures"], 1)

    def test_release_plan_contains_tests_build_and_both_audits(self):
        gates = release_gates(Path("repo"))
        names = {gate.name for gate in gates}
        self.assertTrue(
            {
                "python-tests",
                "frontend-build",
                "python-audit-export",
                "python-dependency-audit",
                "frontend-dependency-audit",
                "diff-check",
            }.issubset(names)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "web").mkdir()
            with patch("api.release_gate.subprocess.run") as runner:
                runner.return_value = MagicMock(returncode=0)
                result = run_gates(
                    root, skip_network_scans=True
                )
        self.assertFalse(result["release_eligible"])
        self.assertEqual(
            {
                item["name"]
                for item in result["gates"]
                if item["status"] == "skipped"
            },
            {
                "python-dependency-audit",
                "frontend-dependency-audit",
            },
        )

    def test_workflow_actions_require_full_commit_shas(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            workflow = workflows / "ci.yml"
            workflow.write_text(
                "steps:\n  - uses: actions/checkout@v4\n",
                encoding="utf-8",
            )
            self.assertFalse(workflow_actions_are_pinned(root))
            workflow.write_text(
                "steps:\n  - uses: actions/checkout@" + "a" * 40 + "\n",
                encoding="utf-8",
            )
            self.assertTrue(workflow_actions_are_pinned(root))
        self.assertTrue(dependency_exceptions_current(date(2026, 8, 30)))
        self.assertFalse(dependency_exceptions_current(date(2026, 8, 31)))

    def test_clamav_stream_protocol_and_fail_closed_result(self):
        connection = MagicMock()
        connection.recv.return_value = b"stream: OK\0"
        context = MagicMock()
        context.__enter__.return_value = connection
        context.__exit__.return_value = False
        with patch(
            "api.upload_scan.socket.create_connection",
            return_value=context,
        ):
            scan_with_clamav(
                b"safe image bytes", host="127.0.0.1", port=3310
            )
        connection.sendall.assert_any_call(b"zINSTREAM\0")

        connection.recv.side_effect = [b"stream: O", b"K\0"]
        with patch(
            "api.upload_scan.socket.create_connection",
            return_value=context,
        ):
            scan_with_clamav(b"fragmented", host="127.0.0.1", port=3310)
        connection.recv.side_effect = None

        connection.recv.return_value = b"stream: Test-Signature FOUND\0"
        with patch(
            "api.upload_scan.socket.create_connection",
            return_value=context,
        ), self.assertRaises(MalwareDetected):
            scan_with_clamav(b"unsafe", host="127.0.0.1", port=3310)

        with patch(
            "api.upload_scan.socket.create_connection",
            side_effect=OSError("offline"),
        ), self.assertRaises(UploadScanError):
            scan_with_clamav(b"data", host="127.0.0.1", port=3310)
        with self.assertRaises(UploadScanError):
            scan_with_clamav(
                b"data",
                host="127.0.0.1",
                port=3310,
                timeout_seconds=float("nan"),
            )


if __name__ == "__main__":
    unittest.main()
