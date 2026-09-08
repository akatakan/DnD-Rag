"""Check that every catalog entry really is on the page it cites.

The catalog's whole claim to trustworthiness is provenance: each entry names a
source document by SHA-256 and the pages it was drawn from. Until the pinned
document was in hand that claim could not be tested, so "curated" meant
"someone said so". This makes it checkable.

Two things are verified:

1. The document on disk hashes to what the entries declare. A citation into a
   document nobody can produce is not a citation.
2. Every entry's name appears on at least one page it cites.

The second check is deliberately loose about two things, because being strict
about them produces false alarms rather than findings: a curator may append a
parenthetical to disambiguate ("Unarmored Defense (Barbarian)") where the SRD
prints only the bare name, and the PDF uses typographic apostrophes where JSON
tends to carry straight ones. Neither difference is a provenance problem.

    uv run python tools/verify_provenance.py
    uv run python tools/verify_provenance.py --catalog data/rulesets/srd-5.2.1/catalog.json

Exits non-zero if anything cannot be verified, so it can gate a release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCUMENT = ROOT / "data" / "SRD_CC_v5.2.1.pdf"
DEFAULT_TARGETS = (
    ROOT / "data" / "rulesets" / "srd-5.2.1" / "catalog.json",
    ROOT / "data" / "catalog-curation" / "srd-5.2.1-foundation-expansion-1.json",
)


def normalise(text: str) -> str:
    """Fold the differences that are typography rather than provenance."""
    text = unicodedata.normalize("NFKD", text)
    for curly, straight in (("’", "'"), ("‘", "'"), ("“", '"'),
                            ("”", '"'), ("–", "-"), ("—", "-")):
        text = text.replace(curly, straight)
    return " ".join(text.casefold().split())


def cited_pages(labels: list[str]) -> list[int]:
    """Expand page labels, including ranges written as '28-29'."""
    pages: list[int] = []
    for label in labels:
        label = str(label).strip()
        if "-" in label:
            first, last = label.split("-", 1)
            pages.extend(range(int(first), int(last) + 1))
        else:
            pages.append(int(label))
    return pages


def load_pages(document: Path) -> tuple[str, list[str]]:
    import pymupdf

    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    with pymupdf.open(document) as pdf:
        return digest, [normalise(page.get_text()) for page in pdf]


def entries_of(payload: dict) -> list[dict]:
    return payload.get("entries", [])


def declared_hash(entry: dict, payload: dict) -> str | None:
    provenance = entry.get("provenance") or {}
    source = entry.get("source") or {}
    return (
        provenance.get("document_sha256")
        or source.get("document_sha256")
        or payload.get("source_document_sha256")
    )


def verify(target: Path, digest: str, pages: list[str]) -> list[str]:
    payload = json.loads(target.read_text(encoding="utf-8"))
    problems: list[str] = []

    # The file's own header claim is checked on its own, not only as a fallback
    # for entries that omit a hash. Otherwise a pack could say it was curated
    # from one document while every entry inside cites another, and nothing
    # would notice.
    pack_hash = payload.get("source_document_sha256")
    if pack_hash and pack_hash != digest:
        problems.append(
            f"dosya basligi baska bir belgeyi gosteriyor ({pack_hash[:12]}…)"
        )

    for entry in entries_of(payload):
        name = entry.get("name", "")
        label = f"{entry.get('type', '?')}:{entry.get('slug', '?')}"

        expected = declared_hash(entry, payload)
        if expected and expected != digest:
            problems.append(f"{label}: belge hash'i tutmuyor ({expected[:12]}…)")
            continue

        provenance = entry.get("provenance") or {}
        labels = provenance.get("page_labels") or []
        if not labels:
            problems.append(f"{label}: sayfa etiketi yok")
            continue

        # A parenthetical suffix is the curator disambiguating, not the source.
        needle = normalise(re.sub(r"\s*\(.*?\)\s*$", "", name))
        found = any(
            needle in pages[index]
            for page in cited_pages(labels)
            # Labels may be printed page numbers or zero-based indices.
            for index in (page - 1, page)
            if 0 <= index < len(pages)
        )
        if not found:
            problems.append(f"{label}: '{name}' {labels} sayfalarinda bulunamadi")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    parser.add_argument("--catalog", type=Path, action="append", dest="catalogs")
    arguments = parser.parse_args()

    document: Path = arguments.document
    if not document.is_file():
        print(f"Kaynak belge yok: {document}", file=sys.stderr)
        print("Provenance dogrulanamaz; kataloga guvenilemez.", file=sys.stderr)
        return 2

    digest, pages = load_pages(document)
    print(f"{document.name}: {len(pages)} sayfa, sha256 {digest[:16]}…")

    failures = 0
    for target in arguments.catalogs or DEFAULT_TARGETS:
        if not target.is_file():
            print(f"  atlandi (yok): {target}")
            continue
        problems = verify(target, digest, pages)
        total = len(entries_of(json.loads(target.read_text(encoding="utf-8"))))
        status = "TAMAM" if not problems else f"{len(problems)} SORUN"
        print(f"  {target.name}: {total} kayit — {status}")
        for problem in problems:
            print(f"    {problem}")
        failures += len(problems)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
