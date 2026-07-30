import unittest
from copy import deepcopy
from pathlib import Path

from api.catalog_curation import (
    CatalogCurationError,
    apply_curation_pack,
    canonical_sha256,
    load_curation_pack,
    validate_curation_pack,
)
from api.rules_catalog import RulesCatalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = (
    PROJECT_ROOT
    / "data"
    / "catalog-curation"
    / "srd-5.2.1-foundation-expansion-1.json"
)


class CatalogCurationTest(unittest.TestCase):
    def setUp(self):
        self.catalog = RulesCatalog(PROJECT_ROOT / "data" / "rulesets")
        self.pack = load_curation_pack(PACK_PATH)

    def test_pack_is_pinned_draft_only_and_schema_v1_bounded(self):
        self.assertEqual(len(self.pack["entries"]), 21)
        self.assertEqual(
            canonical_sha256(self.pack["entries"]),
            self.pack["entries_sha256"],
        )
        self.assertFalse(self.pack["capability"]["builder_ready"])
        self.assertFalse(self.pack["capability"]["publish_allowed"])
        self.assertTrue(self.pack["known_gaps"])
        counts = {}
        for entry in self.pack["entries"]:
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1
            self.assertNotIn("source", entry)
            self.assertNotIn("license", entry)
        self.assertEqual(
            counts,
            {
                "class": 2,
                "feature": 7,
                "species": 4,
                "background": 3,
                "spell": 1,
                "item": 4,
            },
        )

    def test_apply_creates_draft_injects_license_and_is_idempotent(self):
        first = apply_curation_pack(self.catalog, self.pack)

        self.assertTrue(first["created"])
        self.assertEqual(first["publication_status"], "draft")
        self.assertEqual(len(first["changed_entries"]), 21)
        detail = self.catalog.admin_ruleset(self.pack["target_ruleset"])
        self.assertEqual(detail["ruleset"]["entry_count"], 28)
        imported = next(
            entry
            for entry in detail["entries"]
            if entry["id"] == "class:barbarian"
        )
        self.assertEqual(imported["source"]["document_id"], "srd-5.2.1")
        self.assertEqual(imported["license"]["id"], "CC-BY-4.0")
        first_revision = first["revision"]

        second = apply_curation_pack(self.catalog, self.pack)

        self.assertFalse(second["created"])
        self.assertEqual(second["changed_entries"], [])
        self.assertEqual(len(second["unchanged_entries"]), 21)
        self.assertEqual(second["revision"], first_revision)

    def test_draft_only_pack_cannot_publish_or_become_default(self):
        original_versions = self.catalog.admin_versions()

        with self.assertRaisesRegex(CatalogCurationError, "draft-only"):
            apply_curation_pack(
                self.catalog,
                self.pack,
                publish=True,
                make_default=True,
            )

        self.assertEqual(self.catalog.admin_versions(), original_versions)
        self.assertEqual(self.catalog.default_version(), "srd-5.2.1")

    def test_digest_tamper_and_closed_source_fields_fail_closed(self):
        tampered = deepcopy(self.pack)
        tampered["entries"][0]["name"] = "Tampered"
        with self.assertRaisesRegex(CatalogCurationError, "ozeti"):
            validate_curation_pack(tampered)

        closed = deepcopy(self.pack)
        closed["entries"][0]["license"] = {"id": "closed"}
        closed["entries_sha256"] = canonical_sha256(closed["entries"])
        with self.assertRaisesRegex(CatalogCurationError, "yalniz"):
            validate_curation_pack(closed)

    def test_invalid_late_entry_fails_before_draft_creation(self):
        invalid = deepcopy(self.pack)
        invalid["entries"][-1]["data"]["container_capacity_lb"] = -1
        invalid["entries_sha256"] = canonical_sha256(invalid["entries"])

        with self.assertRaisesRegex(CatalogCurationError, "dogrulamasindan"):
            apply_curation_pack(self.catalog, invalid)

        self.assertNotIn(
            invalid["target_ruleset"],
            {version["id"] for version in self.catalog.admin_versions()},
        )

    def test_manual_edit_conflict_does_not_partially_advance_revision(self):
        apply_curation_pack(self.catalog, self.pack)
        detail = self.catalog.admin_ruleset(self.pack["target_ruleset"])
        barbarian = next(
            entry
            for entry in detail["entries"]
            if entry["id"] == "class:barbarian"
        )
        editable = {
            key: deepcopy(barbarian[key])
            for key in ("id", "type", "slug", "name", "data", "provenance")
        }
        editable["name"] = "Developer-edited Barbarian"
        detail = self.catalog.upsert_entry(
            self.pack["target_ruleset"],
            detail["ruleset"]["revision"],
            editable,
        )
        revision_before = detail["ruleset"]["revision"]

        with self.assertRaisesRegex(CatalogCurationError, "sessizce ezilmedi"):
            apply_curation_pack(self.catalog, self.pack)

        after = self.catalog.admin_ruleset(self.pack["target_ruleset"])
        self.assertEqual(after["ruleset"]["revision"], revision_before)
        current = next(
            entry
            for entry in after["entries"]
            if entry["id"] == "class:barbarian"
        )
        self.assertEqual(current["name"], "Developer-edited Barbarian")


if __name__ == "__main__":
    unittest.main()
