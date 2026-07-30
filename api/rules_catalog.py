from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any


ENTITY_TYPES = frozenset(
    {"class", "species", "background", "spell", "feature", "item", "condition"}
)
RULESET_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_LICENSES = frozenset({"CC-BY-4.0"})
ALLOWED_PROVENANCE_METHODS = frozenset({"curated", "derived"})
MAX_CATALOG_BYTES = 25 * 1024 * 1024
MAX_ENTRY_DATA_BYTES = 64 * 1024
MAX_QUERY_LENGTH = 200
MAX_OFFSET = 100_000
OFFICIAL_SOURCE_URL = "https://www.dndbeyond.com/srd"
OFFICIAL_DOCUMENT_URL = (
    "https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf"
)
OFFICIAL_DOCUMENT_SHA256 = (
    "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"
)
OFFICIAL_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/legalcode"
OFFICIAL_ATTRIBUTION = (
    "This work includes material from the System Reference Document 5.2.1 "
    "(“SRD 5.2.1”) by Wizards of the Coast LLC, available at "
    "https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the "
    "Creative Commons Attribution 4.0 International License, available at "
    "https://creativecommons.org/licenses/by/4.0/legalcode."
)
FOUNDATION_ENTRY_SHA256 = {
    "class:fighter": "56fb763d929930dfe847c41da2adb4c1e9e7c812db62c050465611d85f12f223",
    "species:human": "875b682dc2b011397130c692b715ab210f59b5d683116a730c7f08dd58d6b9c2",
    "background:acolyte": "e095355db61e7df420a5285b5e73e012ae678566ec6cd819e32f595551781300",
    "spell:cure-wounds": "055c5df0703a2de003f91024ea10cf7a94c3ebd11919ba59bb9449a279c68147",
    "feature:second-wind": "0adc76152dcd8c73fdd8c17d7a658935cda0a5434f0478a231197de3c643b73b",
    "item:shield": "427a39f767b8b29a36435397889be6a49450f5f9b560ba974aa5d83144b1e123",
    "condition:blinded": "d3496176eff4899a2ac3f122436657fd15e29a29f4d873677cbf2ccb26861195",
}


class CatalogValidationError(ValueError):
    pass


class RulesCatalog:
    """Loads immutable, versioned open-rules catalogs from disk."""

    def __init__(self, root: Path | None = None):
        self.root = (
            root
            if root is not None
            else Path(__file__).resolve().parents[1] / "data" / "rulesets"
        ).resolve()
        self._cache: dict[str, dict[str, Any]] = {}
        self._sorted_entries: dict[str, tuple[dict[str, Any], ...]] = {}
        self._entry_indexes: dict[str, dict[str, dict[str, Any]]] = {}
        self._search_indexes: dict[str, dict[str, str]] = {}
        self._lock = RLock()

    def versions(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        manifests = []
        for path in sorted(self.root.glob("*/catalog.json")):
            version = path.parent.name
            catalog = self._load_internal(version)
            manifests.append(self._summary(catalog))
        return manifests

    def load(self, version: str) -> dict[str, Any]:
        return deepcopy(self._load_internal(version))

    def _load_internal(self, version: str) -> dict[str, Any]:
        if not RULESET_VERSION_PATTERN.fullmatch(version):
            raise CatalogValidationError("Gecersiz ruleset surumu.")
        with self._lock:
            cached = self._cache.get(version)
            if cached is not None:
                return cached

            path = (self.root / version / "catalog.json").resolve()
            try:
                path.relative_to(self.root)
            except ValueError as error:
                raise CatalogValidationError("Ruleset yolu katalog disina cikamaz.") from error
            if not path.is_file():
                raise KeyError(f"Ruleset bulunamadi: {version}")

            if path.stat().st_size > MAX_CATALOG_BYTES:
                raise CatalogValidationError("Ruleset katalog dosyasi boyut sinirini asiyor.")
            with path.open("rb") as catalog_file:
                raw = catalog_file.read(MAX_CATALOG_BYTES + 1)
            if len(raw) > MAX_CATALOG_BYTES:
                raise CatalogValidationError("Ruleset katalog dosyasi boyut sinirini asiyor.")
            try:
                catalog = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CatalogValidationError("Ruleset katalog JSON'u gecersiz.") from error
            self._validate(catalog, version)
            catalog["catalog_sha256"] = hashlib.sha256(raw).hexdigest()
            entries = tuple(
                sorted(
                    catalog["entries"],
                    key=lambda entry: (entry["type"], entry["name"].casefold()),
                )
            )
            self._cache[version] = catalog
            self._sorted_entries[version] = entries
            self._entry_indexes[version] = {
                entry["id"]: entry for entry in entries
            }
            self._search_indexes[version] = {
                entry["id"]: (
                    f"{entry['name']} {entry['slug']} "
                    f"{json.dumps(entry['data'], ensure_ascii=False, sort_keys=True)}"
                ).casefold()
                for entry in entries
            }
            return catalog

    def list_entries(
        self,
        version: str,
        *,
        entity_type: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        if entity_type is not None and entity_type not in ENTITY_TYPES:
            raise CatalogValidationError("Gecersiz katalog varlik tipi.")
        normalized_query = (query or "").strip().casefold()
        if len(normalized_query) > MAX_QUERY_LENGTH:
            raise CatalogValidationError(
                f"Arama sorgusu en fazla {MAX_QUERY_LENGTH} karakter olabilir."
            )
        if offset < 0 or offset > MAX_OFFSET or not 1 <= limit <= 100:
            raise CatalogValidationError(
                f"offset 0..{MAX_OFFSET} ve limit 1..100 araliginda olmali."
            )

        catalog = self._load_internal(version)
        entries = self._sorted_entries[version]
        search_index = self._search_indexes[version]
        entries = [
            entry
            for entry in entries
            if (entity_type is None or entry["type"] == entity_type)
            and (
                not normalized_query
                or normalized_query in search_index[entry["id"]]
            )
        ]
        total = len(entries)
        page = deepcopy(entries[offset : offset + limit])
        return {
            "ruleset": self._summary(catalog),
            "entries": page,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < total,
        }

    def get_entry(self, version: str, entry_id: str) -> dict[str, Any]:
        if len(entry_id) > 80:
            raise CatalogValidationError("Katalog kayit kimligi cok uzun.")
        catalog = self._load_internal(version)
        entry = self._entry_indexes[version].get(entry_id)
        if entry is None:
            raise KeyError(f"Katalog kaydi bulunamadi: {entry_id}")
        return {
            "ruleset": self._summary(catalog),
            "entry": deepcopy(entry),
        }

    @staticmethod
    def _summary(catalog: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": catalog["id"],
            "name": catalog["name"],
            "schema_version": catalog["schema_version"],
            "status": catalog["status"],
            "entry_count": len(catalog["entries"]),
            "entity_types": sorted({entry["type"] for entry in catalog["entries"]}),
            "catalog_sha256": catalog["catalog_sha256"],
            "source": deepcopy(catalog["source"]),
            "license": deepcopy(catalog["license"]),
        }

    @staticmethod
    def _validate(catalog: Any, expected_version: str) -> None:
        if not isinstance(catalog, dict):
            raise CatalogValidationError("Katalog kok nesnesi bir obje olmali.")
        if set(catalog) != {
            "schema_version",
            "id",
            "name",
            "status",
            "source",
            "license",
            "entries",
        }:
            raise CatalogValidationError("Katalog kok alanlari schema ile eslesmiyor.")
        if catalog.get("schema_version") != 1:
            raise CatalogValidationError("Desteklenmeyen katalog schema surumu.")
        if catalog.get("id") != expected_version:
            raise CatalogValidationError("Ruleset kimligi dizin surumuyle eslesmiyor.")
        if catalog.get("status") not in {"foundation", "complete"}:
            raise CatalogValidationError("Katalog durumu foundation veya complete olmali.")

        source = catalog.get("source")
        license_info = catalog.get("license")
        RulesCatalog._validate_source(source)
        RulesCatalog._validate_license(license_info)

        entries = catalog.get("entries")
        if not isinstance(entries, list) or not entries:
            raise CatalogValidationError("Katalog en az bir kayit icermeli.")
        seen_ids: set[str] = set()
        seen_slugs: set[tuple[str, str]] = set()
        present_types: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise CatalogValidationError("Katalog kaydi bir obje olmali.")
            if set(entry) != {
                "id",
                "type",
                "slug",
                "name",
                "data",
                "source",
                "license",
                "provenance",
            }:
                raise CatalogValidationError("Katalog kaydi alanlari schema ile eslesmiyor.")
            entry_id = entry.get("id")
            entity_type = entry.get("type")
            slug = entry.get("slug")
            if (
                not isinstance(entry_id, str)
                or not entry_id
                or not isinstance(slug, str)
                or not slug
                or not isinstance(entry.get("name"), str)
                or not entry["name"].strip()
                or len(entry["name"]) > 120
            ):
                raise CatalogValidationError("Kayit kimligi, slug'i ve adi zorunludur.")
            if entity_type not in ENTITY_TYPES:
                raise CatalogValidationError(f"Gecersiz katalog tipi: {entity_type}")
            if (
                not SLUG_PATTERN.fullmatch(slug)
                or entry_id != f"{entity_type}:{slug}"
            ):
                raise CatalogValidationError(
                    "Kayit kimligi type:canonical-slug biciminde olmali."
                )
            if entry_id in seen_ids or (entity_type, slug) in seen_slugs:
                raise CatalogValidationError("Katalog kimlikleri ve type/slug ciftleri benzersiz olmali.")
            if not isinstance(entry.get("data"), dict):
                raise CatalogValidationError("Kayit data alani bir obje olmali.")
            encoded_data = json.dumps(
                entry["data"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            if len(encoded_data) > MAX_ENTRY_DATA_BYTES:
                raise CatalogValidationError("Katalog kaydi data boyut sinirini asiyor.")
            RulesCatalog._validate_entity_data(entity_type, entry["data"])
            if entry.get("source") != source:
                raise CatalogValidationError("Kayit kaynagi ruleset kaynagiyla eslesmiyor.")
            if entry.get("license") != license_info:
                raise CatalogValidationError("Kayit lisansi ruleset lisansiyla eslesmiyor.")
            RulesCatalog._validate_provenance(entry.get("provenance"), source)
            seen_ids.add(entry_id)
            seen_slugs.add((entity_type, slug))
            present_types.add(entity_type)

        RulesCatalog._validate_references(entries)
        missing = ENTITY_TYPES - present_types
        if missing:
            raise CatalogValidationError(
                f"Katalog zorunlu varlik tiplerini icermiyor: {', '.join(sorted(missing))}"
            )

    @staticmethod
    def _validate_source(source: Any) -> None:
        if not isinstance(source, dict):
            raise CatalogValidationError("Kaynak bilgisi zorunludur.")
        if set(source) != {
            "document_id",
            "title",
            "version",
            "url",
            "document_url",
            "published_at",
            "document_sha256",
        }:
            raise CatalogValidationError("Kaynak alanlari schema ile eslesmiyor.")
        required_strings = ("document_id", "title", "version", "url", "published_at")
        if any(not isinstance(source.get(key), str) or not source[key].strip() for key in required_strings):
            raise CatalogValidationError("Kaynak kimligi, basligi, surumu, URL ve tarihi zorunludur.")
        if source["document_id"] != "srd-5.2.1":
            raise CatalogValidationError("Bundled katalog yalnizca acik SRD 5.2.1 kaynagini kabul eder.")
        if (
            source["url"] != OFFICIAL_SOURCE_URL
            or source.get("document_url") != OFFICIAL_DOCUMENT_URL
            or source.get("document_sha256") != OFFICIAL_DOCUMENT_SHA256
            or not SHA256_PATTERN.fullmatch(str(source.get("document_sha256", "")))
        ):
            raise CatalogValidationError(
                "Kaynak resmi ve sabitlenmis SRD 5.2.1 belgesiyle eslesmiyor."
            )

    @staticmethod
    def _validate_license(license_info: Any) -> None:
        if not isinstance(license_info, dict):
            raise CatalogValidationError("Lisans bilgisi zorunludur.")
        if set(license_info) != {"id", "url", "attribution"}:
            raise CatalogValidationError("Lisans alanlari schema ile eslesmiyor.")
        if license_info.get("id") not in ALLOWED_LICENSES:
            raise CatalogValidationError("Katalog lisansi izin verilen acik lisanslardan degil.")
        if license_info.get("url") != OFFICIAL_LICENSE_URL:
            raise CatalogValidationError("CC BY 4.0 lisans URL'si gecersiz.")
        if license_info.get("attribution") != OFFICIAL_ATTRIBUTION:
            raise CatalogValidationError("Lisans attribution metni resmi sabit metinle eslesmiyor.")

    @staticmethod
    def _validate_provenance(provenance: Any, source: dict[str, Any]) -> None:
        if not isinstance(provenance, dict):
            raise CatalogValidationError("Kayit provenance bilgisi zorunludur.")
        if set(provenance) != {
            "document_id",
            "document_sha256",
            "page_labels",
            "section",
            "method",
        }:
            raise CatalogValidationError("Provenance alanlari schema ile eslesmiyor.")
        pages = provenance.get("page_labels")
        if (
            provenance.get("document_id") != source["document_id"]
            or provenance.get("document_sha256") != source["document_sha256"]
            or provenance.get("method") not in ALLOWED_PROVENANCE_METHODS
            or not isinstance(provenance.get("section"), str)
            or not provenance["section"].strip()
            or len(provenance["section"]) > 200
            or not isinstance(pages, list)
            or not pages
            or len(pages) > 10
            or any(
                not isinstance(page, str)
                or not page.strip()
                or len(page) > 20
                for page in pages
            )
        ):
            raise CatalogValidationError("Kayit provenance zinciri eksik veya gecersiz.")

    @staticmethod
    def _validate_entity_data(entity_type: str, data: dict[str, Any]) -> None:
        schemas: dict[str, tuple[set[str], set[str]]] = {
            "class": (
                {
                    "hit_die",
                    "primary_abilities",
                    "saving_throw_proficiencies",
                    "armor_training",
                    "starting_feature_ids",
                },
                set(),
            ),
            "species": (
                {"creature_type", "size_options", "speed", "traits"},
                set(),
            ),
            "background": (
                {
                    "ability_options",
                    "feat",
                    "skill_proficiencies",
                    "tool_proficiency",
                },
                set(),
            ),
            "spell": (
                {
                    "level",
                    "school",
                    "casting_time",
                    "range",
                    "components",
                    "duration",
                    "healing",
                    "higher_slot",
                },
                set(),
            ),
            "feature": (
                {
                    "class_id",
                    "level",
                    "activation",
                    "effect",
                    "initial_uses",
                    "uses_by_level",
                    "recovery",
                },
                set(),
            ),
            "item": (
                {
                    "category",
                    "armor_class_bonus",
                    "strength_requirement",
                    "stealth_disadvantage",
                    "weight_lb",
                    "cost_gp",
                    "equipment_slot",
                    "armor_training",
                    "requires_attunement",
                    "container_capacity_lb",
                },
                set(),
            ),
            "condition": ({"effects"}, set()),
        }
        required, optional = schemas[entity_type]
        keys = set(data)
        if keys != required | optional:
            raise CatalogValidationError(
                f"{entity_type} data alanlari schema ile eslesmiyor."
            )

        def string_list(value: Any) -> bool:
            return (
                isinstance(value, list)
                and bool(value)
                and all(isinstance(item, str) and item.strip() for item in value)
            )

        if entity_type == "class":
            valid = (
                isinstance(data["hit_die"], int)
                and not isinstance(data["hit_die"], bool)
                and data["hit_die"] in {4, 6, 8, 10, 12}
                and string_list(data["primary_abilities"])
                and string_list(data["saving_throw_proficiencies"])
                and string_list(data["armor_training"])
                and string_list(data["starting_feature_ids"])
            )
        elif entity_type == "species":
            valid = (
                isinstance(data["creature_type"], str)
                and string_list(data["size_options"])
                and isinstance(data["speed"], int)
                and not isinstance(data["speed"], bool)
                and 0 < data["speed"] <= 120
                and string_list(data["traits"])
            )
        elif entity_type == "background":
            valid = (
                string_list(data["ability_options"])
                and isinstance(data["feat"], str)
                and bool(data["feat"].strip())
                and string_list(data["skill_proficiencies"])
                and isinstance(data["tool_proficiency"], str)
                and bool(data["tool_proficiency"].strip())
            )
        elif entity_type == "spell":
            valid = (
                isinstance(data["level"], int)
                and not isinstance(data["level"], bool)
                and 0 <= data["level"] <= 9
                and all(
                    isinstance(data[key], str) and bool(data[key].strip())
                    for key in (
                        "school",
                        "casting_time",
                        "range",
                        "duration",
                        "healing",
                        "higher_slot",
                    )
                )
                and string_list(data["components"])
            )
        elif entity_type == "feature":
            valid = (
                isinstance(data["class_id"], str)
                and isinstance(data["level"], int)
                and not isinstance(data["level"], bool)
                and 1 <= data["level"] <= 20
                and isinstance(data["initial_uses"], int)
                and not isinstance(data["initial_uses"], bool)
                and data["initial_uses"] >= 0
                and data["uses_by_level"] == {"1": 2, "4": 3, "10": 4}
                and all(
                    isinstance(data[key], str) and bool(data[key].strip())
                    for key in ("activation", "effect", "recovery")
                )
            )
        elif entity_type == "item":
            numeric = ("armor_class_bonus", "weight_lb", "cost_gp")
            valid = (
                isinstance(data["category"], str)
                and bool(data["category"].strip())
                and all(
                    isinstance(data[key], (int, float))
                    and not isinstance(data[key], bool)
                    and math.isfinite(data[key])
                    and data[key] >= 0
                    and data[key] <= 1_000_000
                    for key in numeric
                )
                and (
                    data["strength_requirement"] is None
                    or (
                        isinstance(data["strength_requirement"], int)
                        and not isinstance(data["strength_requirement"], bool)
                        and 1 <= data["strength_requirement"] <= 30
                    )
                )
                and isinstance(data["stealth_disadvantage"], bool)
                and data["equipment_slot"]
                in {
                    "armor",
                    "main_hand",
                    "off_hand",
                    "head",
                    "neck",
                    "shoulders",
                    "torso",
                    "hands",
                    "waist",
                    "feet",
                    "ring",
                    "other",
                    None,
                }
                and (
                    data["armor_training"] is None
                    or (
                        isinstance(data["armor_training"], str)
                        and bool(data["armor_training"].strip())
                    )
                )
                and isinstance(data["requires_attunement"], bool)
                and (
                    data["container_capacity_lb"] is None
                    or (
                        isinstance(data["container_capacity_lb"], (int, float))
                        and not isinstance(data["container_capacity_lb"], bool)
                        and math.isfinite(data["container_capacity_lb"])
                        and 0 < data["container_capacity_lb"] <= 1_000_000
                    )
                )
            )
        else:
            valid = string_list(data["effects"])
        if not valid:
            raise CatalogValidationError(f"{entity_type} data degerleri gecersiz.")

    @staticmethod
    def _validate_references(entries: list[dict[str, Any]]) -> None:
        by_id = {entry["id"]: entry for entry in entries}
        for entry in entries:
            references: list[tuple[str, str]] = []
            if entry["type"] == "class":
                references.extend(
                    (reference, "feature")
                    for reference in entry["data"]["starting_feature_ids"]
                )
            elif entry["type"] == "feature":
                references.append((entry["data"]["class_id"], "class"))
            for reference, expected_type in references:
                target = by_id.get(reference)
                if target is None or target["type"] != expected_type:
                    raise CatalogValidationError(
                        f"Katalog referansi eksik veya yanlis tipte: {reference}"
                    )

        if entries and entries[0].get("source", {}).get("document_id") == "srd-5.2.1":
            actual_hashes = {
                entry["id"]: hashlib.sha256(
                    json.dumps(
                        entry,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                for entry in entries
            }
            if actual_hashes != FOUNDATION_ENTRY_SHA256:
                raise CatalogValidationError(
                    "Bundled foundation kayitlari onayli SRD extraction manifestiyle eslesmiyor."
                )
