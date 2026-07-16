import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agent import build_engine
from config import EVALUATION_FILE, RETRIEVAL_TOP_K
from retriever import build_retrievers


def load_cases(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as evaluation_file:
        return yaml.safe_load(evaluation_file) or []


def page_matches(page: object, expected: list[int]) -> bool:
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        return False
    if len(expected) == 2:
        return expected[0] <= page_number <= expected[1]
    return page_number in expected


def evaluate_router(engine, cases: list[dict]) -> dict:
    rows = []
    for index, case in enumerate(cases, start=1):
        expected = set(case["expected_books"])
        predicted = set(engine.route(case["question"]))
        overlap = expected & predicted
        row = {
            "id": case["id"],
            "expected": sorted(expected),
            "predicted": sorted(predicted),
            "exact": predicted == expected,
            "precision": len(overlap) / len(predicted) if predicted else 0.0,
            "recall": len(overlap) / len(expected),
        }
        rows.append(row)
        print(
            f"[router {index:02}/{len(cases)}] {case['id']}: "
            f"{row['predicted']} expected={row['expected']}"
        )

    multi_rows = [row for row in rows if len(row["expected"]) > 1]
    return {
        "count": len(rows),
        "exact_accuracy": sum(row["exact"] for row in rows) / len(rows),
        "macro_precision": sum(row["precision"] for row in rows) / len(rows),
        "macro_recall": sum(row["recall"] for row in rows) / len(rows),
        "multi_book_exact_accuracy": (
            sum(row["exact"] for row in multi_rows) / len(multi_rows)
            if multi_rows
            else None
        ),
        "rows": rows,
    }


def evaluate_retrieval(cases: list[dict], hybrid_enabled: bool) -> dict:
    retrievers, _ = build_retrievers(hybrid_enabled=hybrid_enabled)
    rows = []
    for case in cases:
        for book_id in case["expected_books"]:
            nodes = retrievers[book_id].retrieve(case["question"])
            expected_pages = case["expected_pages"][book_id]
            matching_rank = None
            metadata_valid = bool(nodes)
            returned_pages = []
            for rank, item in enumerate(nodes, start=1):
                metadata = item.node.metadata
                page = metadata.get("page_number")
                returned_pages.append(page)
                metadata_valid = metadata_valid and bool(
                    metadata.get("source_book") and page is not None
                )
                if matching_rank is None and page_matches(page, expected_pages):
                    matching_rank = rank
            rows.append(
                {
                    "id": case["id"],
                    "book": book_id,
                    "hit": matching_rank is not None,
                    "rank": matching_rank,
                    "reciprocal_rank": 1 / matching_rank if matching_rank else 0.0,
                    "metadata_valid": metadata_valid,
                    "returned_pages": returned_pages,
                }
            )
            print(
                f"[retrieval] {case['id']}/{book_id}: "
                f"rank={matching_rank or '-'} pages={returned_pages[:5]}"
            )

    return {
        "mode": "hybrid" if hybrid_enabled else "dense",
        "count": len(rows),
        f"page_hit_rate@{RETRIEVAL_TOP_K}": sum(row["hit"] for row in rows)
        / len(rows),
        "mean_reciprocal_rank": sum(row["reciprocal_rank"] for row in rows)
        / len(rows),
        "source_metadata_rate": sum(row["metadata_valid"] for row in rows)
        / len(rows),
        "rows": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate D&D routing and retrieval")
    parser.add_argument("--provider", choices=["ollama", "gemini"], default="ollama")
    parser.add_argument(
        "--mode", choices=["all", "router", "retrieval"], default="all"
    )
    parser.add_argument(
        "--retrieval", choices=["dense", "hybrid"], default="hybrid"
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> None:
    configure_console()
    args = parse_args()
    cases = load_cases(EVALUATION_FILE)
    if args.limit:
        cases = cases[: args.limit]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "provider": args.provider,
        "case_count": len(cases),
    }

    if args.mode in {"all", "router"}:
        engine = build_engine(args.provider, rerank_enabled=False)
        report["router"] = evaluate_router(engine, cases)
    if args.mode in {"all", "retrieval"}:
        report["retrieval"] = evaluate_retrieval(
            cases, hybrid_enabled=args.retrieval == "hybrid"
        )

    output = args.output or Path("evaluation/results") / (
        f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[done] report: {output}")
    for section in ("router", "retrieval"):
        if section in report:
            summary = {key: value for key, value in report[section].items() if key != "rows"}
            print(f"[{section}] {json.dumps(summary, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
