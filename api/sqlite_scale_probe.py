from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from api.store import GameStore


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * percentile)),
    )
    return ordered[index]


def run_probe(
    *,
    concurrency: int,
    operations_per_worker: int,
    write_ratio: float = 0.2,
    database_path: Path | None = None,
) -> dict[str, Any]:
    if (
        not 1 <= concurrency <= 64
        or not 1 <= operations_per_worker <= 10_000
        or concurrency * operations_per_worker > 100_000
        or not 0 <= write_ratio <= 1
    ):
        raise ValueError("Probe parametreleri gecersiz.")
    temporary = (
        tempfile.TemporaryDirectory()
        if database_path is None
        else None
    )
    path = (
        Path(temporary.name) / "scale-probe.db"
        if temporary is not None
        else database_path.resolve()
    )
    try:
        if path.exists():
            raise FileExistsError(
                "Probe mevcut bir veritabani uzerinde calistirilmaz."
            )
        store = GameStore(
            path,
            auth_pepper="scale-probe-only-pepper",
        )
        created = store.create_game(
            "SQLite Scale Probe", "Probe DM", "human"
        )
        game_id = created["game_id"]
        barrier = threading.Barrier(concurrency)
        latencies: list[float] = []
        read_latencies: list[float] = []
        write_latencies: list[float] = []
        errors: list[str] = []
        read_attempts = 0
        write_attempts = 0
        result_lock = threading.Lock()

        def worker(worker_index: int) -> None:
            nonlocal read_attempts, write_attempts
            local_latencies = []
            local_read_latencies = []
            local_write_latencies = []
            local_errors = []
            local_read_attempts = 0
            local_write_attempts = 0
            barrier.wait()
            for operation in range(operations_per_worker):
                started = time.perf_counter()
                global_index = operation * concurrency + worker_index
                is_write = (
                    math.floor((global_index + 1) * write_ratio)
                    > math.floor(global_index * write_ratio)
                )
                if is_write:
                    local_write_attempts += 1
                else:
                    local_read_attempts += 1
                try:
                    if is_write:
                        with store.transaction():
                            with store.connect() as db:
                                db.execute(
                                    """
                                    UPDATE games
                                    SET updated_at = ?
                                    WHERE id = ?
                                    """,
                                    (str(time.time_ns()), game_id),
                                )
                    else:
                        store.game(game_id)
                except Exception as error:  # reported, not hidden
                    local_errors.append(type(error).__name__)
                finally:
                    latency = (time.perf_counter() - started) * 1000
                    local_latencies.append(latency)
                    (
                        local_write_latencies
                        if is_write
                        else local_read_latencies
                    ).append(latency)
            with result_lock:
                latencies.extend(local_latencies)
                read_latencies.extend(local_read_latencies)
                write_latencies.extend(local_write_latencies)
                errors.extend(local_errors)
                read_attempts += local_read_attempts
                write_attempts += local_write_attempts

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(worker, index)
                for index in range(concurrency)
            ]
            for future in futures:
                future.result()
        elapsed = time.perf_counter() - started
        operation_count = concurrency * operations_per_worker
        successful_operations = operation_count - len(errors)
        actual_write_ratio = write_attempts / operation_count

        def latency_summary(values: list[float]) -> dict[str, float]:
            return {
                "mean": round(statistics.fmean(values), 3)
                if values else 0.0,
                "p50": round(_percentile(values, 0.50), 3),
                "p95": round(_percentile(values, 0.95), 3),
                "p99": round(_percentile(values, 0.99), 3),
                "max": round(max(values), 3) if values else 0.0,
            }

        with store.connect() as db:
            journal_mode = db.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
        return {
            "concurrency": concurrency,
            "operations": operation_count,
            "successful_operations": successful_operations,
            "requested_write_ratio": write_ratio,
            "actual_write_ratio": round(actual_write_ratio, 6),
            "read_attempts": read_attempts,
            "write_attempts": write_attempts,
            "elapsed_seconds": round(elapsed, 6),
            "throughput_ops_per_second": round(
                operation_count / elapsed, 2
            ),
            "successful_throughput_ops_per_second": round(
                successful_operations / elapsed, 2
            ),
            "latency_ms": latency_summary(latencies),
            "read_latency_ms": latency_summary(read_latencies),
            "write_latency_ms": latency_summary(write_latencies),
            "errors": len(errors),
            "error_rate": round(len(errors) / operation_count, 6),
            "error_types": {
                kind: errors.count(kind) for kind in sorted(set(errors))
            },
            "journal_mode": str(journal_mode).lower(),
            "execution_model": "threads",
            "workload": "single_game_durable_metadata_update",
        }
    finally:
        if temporary is not None:
            temporary.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tetsu SQLite concurrent read/write probe."
    )
    parser.add_argument(
        "--concurrency",
        default="1,2,4,8",
        help="Virgulle ayrilmis worker sayilari.",
    )
    parser.add_argument("--operations", type=int, default=100)
    parser.add_argument("--write-ratio", type=float, default=0.2)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    levels = [
        int(value.strip())
        for value in arguments.concurrency.split(",")
        if value.strip()
    ]
    report = {
        "probe_version": 2,
        "generated_at_epoch": time.time(),
        "results": [
            run_probe(
                concurrency=level,
                operations_per_worker=arguments.operations,
                write_ratio=arguments.write_ratio,
            )
            for level in levels
        ],
    }
    content = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(content + "\n", encoding="utf-8")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
