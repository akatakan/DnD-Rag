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


@dataclass(frozen=True)
class DependencyException:
    """One time-boxed audit ignore for an advisory with no upstream fix."""

    advisory_id: str
    package: str
    expires_on: date


# Single source of truth for audit ignores: the local release gate and the CI
# workflow both read this list, so an ignore flag can never outlive its expiry
# date. Every entry needs an owner, risk assessment and compensating control in
# docs/production-operations.md. An empty tuple ignores nothing.
_DEPENDENCY_EXCEPTIONS: tuple[DependencyException, ...] = (
    DependencyException("GHSA-8mgp-746c-j5xp", "nltk", date(2026, 12, 1)),
)


def audit_ignore_flags() -> tuple[str, ...]:
    """pip-audit flags for the documented exceptions, empty when there are none."""
    flags: list[str] = []
    for exception in _DEPENDENCY_EXCEPTIONS:
        flags.extend(("--ignore-vuln", exception.advisory_id))
    return tuple(flags)


def expired_dependency_exceptions(
    current: date | None = None,
) -> tuple[DependencyException, ...]:
    """Exceptions past their recheck date; non-empty means the gate fails closed."""
    today = current or date.today()
    return tuple(
        exception
        for exception in _DEPENDENCY_EXCEPTIONS
        if today > exception.expires_on
    )


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
                *audit_ignore_flags(),
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
    """Fail closed once a documented temporary advisory exception expires."""
    return not expired_dependency_exceptions(current)


def run_gates(
    root: Path,
    *,
    skip_network_scans: bool = False,
    today: date | None = None,
) -> dict:
    audit_requirements = root / "runtime" / "release-audit-requirements.txt"
    audit_requirements.parent.mkdir(parents=True, exist_ok=True)
    outcomes = []
    try:
        if not dependency_exceptions_current(today):
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
    parser.add_argument(
        "--print-audit-ignores",
        action="store_true",
        help="pip-audit ignore bayraklarini yazar; CI komutu bunu tuketir.",
    )
    arguments = parser.parse_args()
    if arguments.print_audit_ignores:
        print(" ".join(audit_ignore_flags()))
        return 0
    if arguments.check_policy_only:
        expired = expired_dependency_exceptions()
        for exception in expired:
            print(
                f"{exception.advisory_id} ({exception.package}) istisnasinin "
                f"suresi {exception.expires_on} tarihinde doldu; advisory'yi "
                "yeniden degerlendirin.",
                file=sys.stderr,
            )
        return 1 if expired else 0
    result = run_gates(
        arguments.root.resolve(),
        skip_network_scans=arguments.skip_network_scans,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["release_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
