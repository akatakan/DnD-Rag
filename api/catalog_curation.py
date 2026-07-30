from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from api.rules_catalog import (
    OFFICIAL_DOCUMENT_SHA256,
    CatalogValidationError,
    RulesCatalog,
)


MAX_CURATION_PACK_BYTES = 5 * 1024 * 1024
PACK_KEYS = {
    "schema_version",
    "pack_id",
    "source_ruleset",
    "target_ruleset",
    "target_name",
    "source_document_id",
    "source_document_sha256",
    "capability",
    "known_gaps",
    "entries_sha256",
    "entries",
}
ENTRY_KEYS = {"id", "type", "slug", "name", "data", "provenance"}
PROVENANCE_KEYS = {
    "document_id",
    "document_sha256",
    "page_labels",
    "section",
    "method",
}


class CatalogCurationError(ValueError):
    """Raised when a curation pack cannot be trusted or applied safely."""


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def load_curation_pack(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CatalogCurationError(f"Curation pack bulunamadi: {path}")
    if path.stat().st_size > MAX_CURATION_PACK_BYTES:
        raise CatalogCurationError("Curation pack boyut sinirini asiyor.")
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogCurationError("Curation pack okunamadi veya JSON gecersiz.") from error
    validate_curation_pack(pack)
    return pack


def validate_curation_pack(pack: Any) -> None:
    if not isinstance(pack, dict) or set(pack) != PACK_KEYS:
        raise CatalogCurationError("Curation pack alanlari schema ile eslesmiyor.")
    if pack["schema_version"] != 1:
        raise CatalogCurationError("Desteklenmeyen curation pack schema surumu.")
    for field in ("pack_id", "source_ruleset", "target_ruleset", "target_name"):
        if not isinstance(pack[field], str) or not pack[field].strip():
            raise CatalogCurationError(f"{field} zorunlu bir metin olmali.")
    if pack["source_ruleset"] == pack["target_ruleset"]:
        raise CatalogCurationError("Curation hedefi yayinlanmis kaynakla ayni olamaz.")
    if pack["source_document_id"] != "srd-5.2.1":
        raise CatalogCurationError("Yalnizca acik SRD 5.2.1 curation pack'i kabul edilir.")
    if pack["source_document_sha256"] != OFFICIAL_DOCUMENT_SHA256:
        raise CatalogCurationError("Curation pack sabitlenmis SRD belgesiyle eslesmiyor.")
    capability = pack["capability"]
    if (
        not isinstance(capability, dict)
        or set(capability)
        != {"classification", "builder_ready", "publish_allowed", "notes"}
        or capability["classification"] != "schema-v1-compatible-curation"
        or not isinstance(capability["builder_ready"], bool)
        or not isinstance(capability["publish_allowed"], bool)
        or not isinstance(capability["notes"], str)
        or not capability["notes"].strip()
    ):
        raise CatalogCurationError("Curation capability bildirimi gecersiz.")
    if (
        not isinstance(pack["known_gaps"], list)
        or not pack["known_gaps"]
        or any(
            not isinstance(gap, str) or not gap.strip()
            for gap in pack["known_gaps"]
        )
    ):
        raise CatalogCurationError("Curation pack known_gaps bildirimi zorunludur.")
    entries = pack["entries"]
    if not isinstance(entries, list) or not entries:
        raise CatalogCurationError("Curation pack en az bir kayit icermeli.")
    if canonical_sha256(entries) != pack["entries_sha256"]:
        raise CatalogCurationError("Curation pack kayit ozeti eslesmiyor.")

    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise CatalogCurationError(
                "Curation kaydi yalniz id/type/slug/name/data/provenance icermeli."
            )
        if entry["id"] in seen:
            raise CatalogCurationError(f"Tekrarlanan curation kaydi: {entry['id']}")
        seen.add(entry["id"])
        provenance = entry["provenance"]
        if not isinstance(provenance, dict) or set(provenance) != PROVENANCE_KEYS:
            raise CatalogCurationError("Curation provenance alanlari gecersiz.")
        if (
            provenance["document_id"] != pack["source_document_id"]
            or provenance["document_sha256"] != pack["source_document_sha256"]
            or provenance["method"] not in {"curated", "derived"}
        ):
            raise CatalogCurationError(
                f"{entry['id']} sabitlenmis SRD provenance zinciriyle eslesmiyor."
            )


def _entry_projection(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(entry[key]) for key in ENTRY_KEYS}


def preflight_curation_pack(
    catalog: RulesCatalog, pack: dict[str, Any]
) -> None:
    """Validate every entry and cross-reference before creating a draft."""

    validate_curation_pack(pack)
    try:
        source_catalog = catalog.load(pack["source_ruleset"])
    except (CatalogValidationError, KeyError) as error:
        raise CatalogCurationError("Published kaynak ruleset dogrulanamadi.") from error
    source = source_catalog["source"]
    license_info = source_catalog["license"]
    if (
        source.get("document_id") != pack["source_document_id"]
        or source.get("document_sha256") != pack["source_document_sha256"]
    ):
        raise CatalogCurationError("Pack provenance'i kaynak ruleset ile eslesmiyor.")

    merged = {
        entry["id"]: deepcopy(entry) for entry in source_catalog["entries"]
    }
    try:
        for entry in pack["entries"]:
            merged[entry["id"]] = RulesCatalog._normalize_admin_entry(
                entry, source, license_info
            )
        RulesCatalog._validate_references(list(merged.values()))
    except CatalogValidationError as error:
        raise CatalogCurationError(
            f"Curation pack katalog dogrulamasindan gecemedi: {error}"
        ) from error


def apply_curation_pack(
    catalog: RulesCatalog,
    pack: dict[str, Any],
    *,
    publish: bool = False,
    make_default: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Merge a pinned SRD pack into a versioned draft without silent overwrites."""

    validate_curation_pack(pack)
    if publish and not pack["capability"]["publish_allowed"]:
        raise CatalogCurationError(
            "Bu pack schema bosluklari nedeniyle draft-only; yayinlama developer "
            "incelemesi ve schema degisikligi sonrasina birakildi."
        )
    preflight_curation_pack(catalog, pack)
    versions = {item["id"]: item for item in catalog.admin_versions()}
    target_id = pack["target_ruleset"]
    target = versions.get(target_id)
    created = False
    if target is None:
        detail = catalog.clone_ruleset(
            pack["source_ruleset"],
            target_id,
            pack["target_name"],
        )
        created = True
    else:
        detail = catalog.admin_ruleset(target_id)
        ruleset = detail["ruleset"]
        if ruleset["based_on"] != pack["source_ruleset"]:
            raise CatalogCurationError("Hedef ruleset farkli bir kaynaktan turetilmis.")
        if ruleset["name"] != pack["target_name"]:
            raise CatalogCurationError("Hedef ruleset adi curation pack ile eslesmiyor.")

    ruleset = detail["ruleset"]
    existing = {
        entry["id"]: _entry_projection(entry) for entry in detail["entries"]
    }
    changed: list[str] = []
    unchanged: list[str] = []
    conflicts: list[str] = []

    if ruleset["publication_status"] == "published":
        for desired in pack["entries"]:
            current = existing.get(desired["id"])
            if current == desired:
                unchanged.append(desired["id"])
            else:
                conflicts.append(desired["id"])
        if conflicts:
            raise CatalogCurationError(
                "Yayinlanmis hedef curation pack ile eslesmiyor: "
                + ", ".join(conflicts)
            )
        return {
            "pack_id": pack["pack_id"],
            "entries_sha256": pack["entries_sha256"],
            "target_ruleset": target_id,
            "created": False,
            "publication_status": "published",
            "changed_entries": changed,
            "unchanged_entries": unchanged,
            "revision": ruleset["revision"],
            "capability": deepcopy(pack["capability"]),
            "known_gaps": deepcopy(pack["known_gaps"]),
        }

    if not overwrite:
        conflicts = [
            desired["id"]
            for desired in pack["entries"]
            if desired["id"] in existing
            and existing[desired["id"]] != desired
        ]
        if conflicts:
            raise CatalogCurationError(
                "Mevcut draft kayitlari sessizce ezilmedi; --overwrite gerekli: "
                + ", ".join(conflicts)
            )

    revision = int(ruleset["revision"])
    for desired in pack["entries"]:
        current = existing.get(desired["id"])
        if current == desired:
            unchanged.append(desired["id"])
            continue
        try:
            detail = catalog.upsert_entry(target_id, revision, desired)
        except CatalogValidationError as error:
            raise CatalogCurationError(
                f"{desired['id']} katalog dogrulamasindan gecemedi: {error}"
            ) from error
        revision = int(detail["ruleset"]["revision"])
        changed.append(desired["id"])

    if publish:
        try:
            detail = catalog.publish_ruleset(target_id, revision, make_default)
        except CatalogValidationError as error:
            raise CatalogCurationError(
                f"Draft yayin dogrulamasindan gecemedi: {error}"
            ) from error
        revision = int(detail["ruleset"]["revision"])

    return {
        "pack_id": pack["pack_id"],
        "entries_sha256": pack["entries_sha256"],
        "target_ruleset": target_id,
        "created": created,
        "publication_status": "published" if publish else "draft",
        "changed_entries": changed,
        "unchanged_entries": unchanged,
        "revision": revision,
        "capability": deepcopy(pack["capability"]),
        "known_gaps": deepcopy(pack["known_gaps"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pinned SRD 5.2.1 curation pack'ini DB-backed draft'a uygula."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--make-default", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.make_default and not args.publish:
        raise SystemExit("--make-default yalnizca --publish ile kullanilabilir.")
    if not args.database.is_file():
        raise SystemExit(
            "Veritabani bulunamadi. API'yi en az bir kez baslatip migration v27'yi uygulayin."
        )
    pack = load_curation_pack(args.pack)
    result = apply_curation_pack(
        RulesCatalog(database_path=args.database),
        pack,
        publish=args.publish,
        make_default=args.make_default,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
