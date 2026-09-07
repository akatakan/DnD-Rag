"""Catalog schema v2: subclasses, version isolation and the migration path.

docs/catalog-schema-v2-prerequisites.md sets the rules this file enforces:
an explicit schema version with a migration path, no reinterpretation of
already-published v1 rows, typed references that reject dangling or
wrong-type targets, and rejection of malformed or future versions.
"""

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from api.migrations import MIGRATIONS, apply_migrations
from api.rules_catalog import (
    ENTITY_TYPES,
    ENTITY_TYPES_BY_SCHEMA,
    SCHEMA_VERSIONS,
    CatalogValidationError,
    RulesCatalog,
)
from api.store import GameStore

SOURCE = {
    "document_id": "srd-5.2.1",
    "title": "Test source",
    "version": "5.2.1",
    "url": "https://example.test/srd",
    "document_url": "https://example.test/srd.pdf",
    "published_at": "2026-01-01",
    "document_sha256": "a" * 64,
}
LICENSE = {"id": "CC-BY-4.0", "name": "CC BY 4.0", "url": "https://example.test"}
PROVENANCE = {
    "document_id": "srd-5.2.1",
    "document_sha256": "a" * 64,
    "page_labels": ["1"],
    "section": "Test",
    "method": "curated",
}


def entry(entity_type: str, slug: str, data: dict) -> dict:
    return {
        "id": f"{entity_type}:{slug}",
        "type": entity_type,
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "data": data,
        "source": SOURCE,
        "license": LICENSE,
        "provenance": PROVENANCE,
    }


class CatalogSchemaV2Test(unittest.TestCase):
    def test_v2_adds_subclass_and_v1_keeps_exactly_its_own_shapes(self):
        self.assertEqual(SCHEMA_VERSIONS, {1, 2})
        self.assertEqual(ENTITY_TYPES_BY_SCHEMA[1], ENTITY_TYPES)
        self.assertEqual(
            ENTITY_TYPES_BY_SCHEMA[2] - ENTITY_TYPES_BY_SCHEMA[1], {"subclass"}
        )
        # A v1 ruleset must never be reinterpreted as carrying subclasses.
        self.assertNotIn("subclass", ENTITY_TYPES_BY_SCHEMA[1])

    def test_a_subclass_record_is_descriptive_only(self):
        # Anything that looks like an executable operation is refused: the
        # engines stay authoritative for rolls, resources and HP.
        RulesCatalog._validate_subclass_data(
            {
                "class_id": "class:fighter",
                "unlock_level": 3,
                "feature_ids": ["feature:extra-strike"],
                "summary": "Bir arketip.",
            }
        )
        with self.assertRaises(CatalogValidationError):
            RulesCatalog._validate_subclass_data(
                {
                    "class_id": "class:fighter",
                    "unlock_level": 3,
                    "feature_ids": [],
                    "summary": "Bir arketip.",
                    "effects": [{"op": "add_hp", "value": 5}],
                }
            )

    def test_a_subclass_field_is_bounded(self):
        base = {
            "class_id": "class:fighter",
            "unlock_level": 3,
            "feature_ids": [],
            "summary": "Bir arketip.",
        }
        for override, label in (
            ({"class_id": "feature:oops"}, "class olmayan class_id"),
            ({"unlock_level": 0}, "seviye 0"),
            ({"unlock_level": 21}, "seviye 21"),
            ({"unlock_level": True}, "bool seviye"),
            ({"feature_ids": ["class:fighter"]}, "feature olmayan referans"),
            ({"feature_ids": ["feature:a", "feature:a"]}, "tekrar eden feature"),
            ({"summary": ""}, "bos ozet"),
            ({"summary": "x" * 601}, "asiri uzun ozet"),
        ):
            with self.subTest(label):
                with self.assertRaises(CatalogValidationError):
                    RulesCatalog._validate_subclass_data({**base, **override})

    def test_a_subclass_reference_must_resolve_to_the_right_type(self):
        entries = [
            entry("class", "fighter", {"starting_feature_ids": [], "spellcasting": None}),
            entry(
                "subclass",
                "champion",
                {
                    "class_id": "class:missing",
                    "unlock_level": 3,
                    "feature_ids": [],
                    "summary": "Dangling.",
                },
            ),
        ]
        with self.assertRaises(CatalogValidationError):
            RulesCatalog._validate_references(entries, 2)

        # Pointing at a real entry of the wrong type is refused just as firmly.
        entries[1]["data"]["class_id"] = "class:fighter"
        entries[1]["data"]["feature_ids"] = ["class:fighter"]
        with self.assertRaises(CatalogValidationError):
            RulesCatalog._validate_references(entries, 2)

    def test_a_subclass_is_rejected_inside_a_v1_catalog(self):
        subclass = entry(
            "subclass",
            "champion",
            {
                "class_id": "class:fighter",
                "unlock_level": 3,
                "feature_ids": [],
                "summary": "v1 bunu tasiyamaz.",
            },
        )
        with self.assertRaises(CatalogValidationError):
            RulesCatalog._validate_entry(subclass, SOURCE, LICENSE, 1)
        # The same record is acceptable once the ruleset declares v2.
        RulesCatalog._validate_entry(subclass, SOURCE, LICENSE, 2)

    def test_an_unknown_schema_version_is_refused(self):
        catalog = {
            "schema_version": 3,
            "id": "test-ruleset",
            "name": "Test",
            "status": "foundation",
            "source": SOURCE,
            "license": LICENSE,
            "entries": [entry("condition", "blinded", {"summary": "x"})],
        }
        for version in (0, 3, "2", None, 1.0):
            with self.subTest(version=version):
                broken = copy.deepcopy(catalog)
                broken["schema_version"] = version
                with self.assertRaises(CatalogValidationError):
                    RulesCatalog._validate(broken, "test-ruleset")


class CatalogSchemaV2MigrationTest(unittest.TestCase):
    def test_the_migration_widens_the_constraint_without_losing_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "game.db"
            GameStore(path)
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            try:
                entries_before = db.execute(
                    "SELECT COUNT(*) AS count FROM ruleset_entries"
                ).fetchone()["count"]
                rulesets_before = db.execute(
                    "SELECT COUNT(*) AS count FROM rulesets"
                ).fetchone()["count"]
                self.assertGreater(entries_before, 0)

                # Rewind to the pre-v2 constraint and re-run the migration, the
                # way an existing deployment upgrades.
                version = next(
                    number
                    for number, name, _ in MIGRATIONS
                    if name == "catalog_schema_v2"
                )
                db.execute("PRAGMA foreign_keys = ON")
                db.execute(
                    "DELETE FROM schema_migrations WHERE version >= ?", (version,)
                )
                db.commit()

                apply_migrations(db)
                db.commit()

                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) AS count FROM ruleset_entries"
                    ).fetchone()["count"],
                    entries_before,
                    "migration dropped catalog entries",
                )
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) AS count FROM rulesets"
                    ).fetchone()["count"],
                    rulesets_before,
                )
                sql = db.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'rulesets'"
                ).fetchone()["sql"]
                self.assertIn("schema_version IN (1, 2)", sql)
                self.assertEqual(
                    db.execute("PRAGMA foreign_key_check").fetchall(), []
                )
            finally:
                db.close()

    def test_the_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "game.db"
            GameStore(path)
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            try:
                before = db.execute(
                    "SELECT COUNT(*) AS count FROM ruleset_entries"
                ).fetchone()["count"]
                apply_migrations(db)
                apply_migrations(db)
                db.commit()
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) AS count FROM ruleset_entries"
                    ).fetchone()["count"],
                    before,
                )
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
