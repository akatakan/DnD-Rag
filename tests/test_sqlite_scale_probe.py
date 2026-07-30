import tempfile
import unittest
from pathlib import Path

from api.sqlite_scale_probe import run_probe


class SQLiteScaleProbeTest(unittest.TestCase):
    def test_probe_reports_bounded_concurrent_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            report = run_probe(
                concurrency=3,
                operations_per_worker=8,
                write_ratio=0.25,
                database_path=Path(temp) / "probe.db",
            )
        self.assertEqual(report["operations"], 24)
        self.assertEqual(report["successful_operations"], 24)
        self.assertEqual(report["write_attempts"], 6)
        self.assertEqual(report["read_attempts"], 18)
        self.assertEqual(report["actual_write_ratio"], 0.25)
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["error_rate"], 0)
        self.assertEqual(report["error_types"], {})
        self.assertEqual(report["journal_mode"], "wal")
        self.assertEqual(report["execution_model"], "threads")
        self.assertGreater(report["throughput_ops_per_second"], 0)
        self.assertGreaterEqual(
            report["latency_ms"]["p99"],
            report["latency_ms"]["p50"],
        )

    def test_probe_rejects_unbounded_arguments(self):
        with self.assertRaises(ValueError):
            run_probe(concurrency=0, operations_per_worker=1)
        with self.assertRaises(ValueError):
            run_probe(concurrency=1, operations_per_worker=10_001)
        with self.assertRaises(ValueError):
            run_probe(concurrency=64, operations_per_worker=2_000)
        with self.assertRaises(ValueError):
            run_probe(
                concurrency=1,
                operations_per_worker=1,
                write_ratio=1.1,
            )

    def test_probe_refuses_to_mutate_existing_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "existing.db"
            path.write_bytes(b"operator-data")
            with self.assertRaises(FileExistsError):
                run_probe(
                    concurrency=1,
                    operations_per_worker=1,
                    database_path=path,
                )
            self.assertEqual(path.read_bytes(), b"operator-data")


if __name__ == "__main__":
    unittest.main()
