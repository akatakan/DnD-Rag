from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_ACTION_REF = re.compile(r"^\s*-\s+uses:\s+[^@\s]+@([^\s#]+)", re.MULTILINE)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_DEPENDENCY_EXCEPTION_EXPIRY = date(2026, 8, 30)


@dataclass(frozen=True)
class Gate:
    name: str
    command: tuple[str, ...]
    cwd: Path
    network_scan: bool = False


def release_gates(root: Path) -> list[Gate]:
    audit_requirements = root / "runtime" / "release-audit-requirements.txt"
    return [
        Gate("lock", ("uv", "lock", "--check"), root),
        Gate(
            "python-tests",
            (sys.executable, "-m", "pytest", "-q"),
            root,
        ),
        Gate("frontend-lock", ("npm", "ci"), root / "web"),
        Gate("frontend-build", ("npm", "run", "build"), root / "web"),
        Gate(
            "python-audit-export",
            (
                "uv",
                "export",
                "--quiet",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(audit_requirements),
            ),
            root,
        ),
        Gate(
            "python-dependency-audit",
            (
                "uv",
                "tool",
                "run",
                "pip-audit==2.10.1",
                "-r",
                str(audit_requirements),
                "--ignore-vuln",
                "PYSEC-2026-597",
            ),
            root,
            True,
        ),
        Gate(
            "frontend-dependency-audit",
            ("npm", "audit", "--omit=dev", "--audit-level=high"),
            root / "web",
            True,
        ),
        Gate("diff-check", ("git", "diff", "--check"), root),
    ]


def workflow_actions_are_pinned(root: Path) -> bool:
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.exists():
        return True
    refs = []
    for path in (*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")):
        refs.extend(_ACTION_REF.findall(path.read_text(encoding="utf-8")))
    return bool(refs) and all(_COMMIT_SHA.fullmatch(ref) for ref in refs)


def dependency_exceptions_current(current: date | None = None) -> bool:
    """Fail closed once the documented temporary advisory exception expires."""
    return (current or date.today()) <= _DEPENDENCY_EXCEPTION_EXPIRY


def run_gates(root: Path, *, skip_network_scans: bool = False) -> dict:
    audit_requirements = root / "runtime" / "release-audit-requirements.txt"
    audit_requirements.parent.mkdir(parents=True, exist_ok=True)
    outcomes = []
    try:
        if not dependency_exceptions_current():
            return {
                "release_eligible": False,
                "gates": [{
                    "name": "dependency-exception-expiry",
                    "status": "failed",
                    "exit_code": 1,
                }],
            }
        outcomes.append({
            "name": "dependency-exception-expiry",
            "status": "passed",
            "exit_code": 0,
        })
        if not workflow_actions_are_pinned(root):
            return {
                "release_eligible": False,
                "gates": [{
                    "name": "workflow-action-pins",
                    "status": "failed",
                    "exit_code": 1,
                }],
            }
        outcomes.append({
            "name": "workflow-action-pins",
            "status": "passed",
            "exit_code": 0,
        })
        for gate in release_gates(root):
            if gate.network_scan and skip_network_scans:
                outcomes.append({"name": gate.name, "status": "skipped"})
                continue
            completed = subprocess.run(
                gate.command,
                cwd=gate.cwd,
                check=False,
            )
            outcomes.append(
                {
                    "name": gate.name,
                    "status": (
                        "passed" if completed.returncode == 0 else "failed"
                    ),
                    "exit_code": completed.returncode,
                }
            )
            if completed.returncode:
                break
    finally:
        audit_requirements.unlink(missing_ok=True)
    return {
        "release_eligible": all(
            item["status"] == "passed" for item in outcomes
        ),
        "gates": outcomes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Tetsu release gates.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--skip-network-scans",
        action="store_true",
        help="Local smoke only; result is never release eligible.",
    )
    parser.add_argument(
        "--check-policy-only",
        action="store_true",
        help="CI icin expiring security-policy kontrolu.",
    )
    arguments = parser.parse_args()
    if arguments.check_policy_only:
        return 0 if dependency_exceptions_current() else 1
    result = run_gates(
        arguments.root.resolve(),
        skip_network_scans=arguments.skip_network_scans,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["release_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
