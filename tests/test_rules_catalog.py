import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

from api.rules_catalog import (
    CatalogValidationError,
    ENTITY_TYPES,
    MAX_CATALOG_BYTES,
    RulesCatalog,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_CATALOG = (
    PROJECT_ROOT / "data" / "rulesets" / "srd-5.2.1" / "catalog.json"
)


class RulesCatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = RulesCatalog(PROJECT_ROOT / "data" / "rulesets")

    def test_bundled_catalog_covers_required_types_with_full_provenance(self):
        loaded = self.catalog.load("srd-5.2.1")

        self.assertEqual(loaded["status"], "foundation")
        self.assertEqual(
            {entry["type"] for entry in loaded["entries"]},
            set(ENTITY_TYPES),
        )
        self.assertRegex(loaded["catalog_sha256"], r"^[0-9a-f]{64}$")
        for entry in loaded["entries"]:
            self.assertEqual(entry["source"], loaded["source"])
            self.assertEqual(entry["license"], loaded["license"])
            self.assertEqual(entry["license"]["id"], "CC-BY-4.0")
            self.assertEqual(
                entry["provenance"]["document_sha256"],
                loaded["source"]["document_sha256"],
            )
            self.assertTrue(entry["provenance"]["page_labels"])
            self.assertTrue(entry["provenance"]["section"])

    def test_filter_search_pagination_and_single_lookup(self):
        spells = self.catalog.list_entries(
            "srd-5.2.1", entity_type="spell", query="healing", limit=10
        )
        self.assertEqual(spells["total"], 1)
        self.assertEqual(spells["entries"][0]["id"], "spell:cure-wounds")

        first_page = self.catalog.list_entries("srd-5.2.1", offset=0, limit=2)
        second_page = self.catalog.list_entries("srd-5.2.1", offset=2, limit=2)
        self.assertTrue(first_page["has_more"])
        self.assertTrue(
            {entry["id"] for entry in first_page["entries"]}.isdisjoint(
                {entry["id"] for entry in second_page["entries"]}
            )
        )
        found = self.catalog.get_entry("srd-5.2.1", "feature:second-wind")
        self.assertEqual(found["entry"]["data"]["class_id"], "class:fighter")

    def test_load_returns_a_copy_not_mutable_cached_state(self):
        first = self.catalog.load("srd-5.2.1")
        first["entries"].clear()
        second = self.catalog.load("srd-5.2.1")
        self.assertEqual(len(second["entries"]), 7)

    def test_parallel_queries_share_immutable_indexes_safely(self):
        def query(index):
            return self.catalog.list_entries(
                "srd-5.2.1",
                entity_type="feature" if index % 2 else None,
                query="wind" if index % 2 else "",
                limit=10,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(query, range(40)))

        self.assertTrue(
            all(result["ruleset"]["id"] == "srd-5.2.1" for result in results)
        )
        self.assertTrue(
            all(
                result["entries"][0]["id"] == "feature:second-wind"
                for index, result in enumerate(results)
                if index % 2
            )
        )

    def test_rejects_path_traversal_and_invalid_query_bounds(self):
        with self.assertRaises(CatalogValidationError):
            self.catalog.load("../srd-5.2.1")
        with self.assertRaises(CatalogValidationError):
            self.catalog.list_entries("srd-5.2.1", entity_type="monster")
        with self.assertRaises(CatalogValidationError):
            self.catalog.list_entries("srd-5.2.1", limit=101)
        with self.assertRaises(CatalogValidationError):
            self.catalog.list_entries("srd-5.2.1", query="x" * 201)

    def test_rejects_closed_license_duplicate_and_broken_provenance(self):
        base = json.loads(BUNDLED_CATALOG.read_text(encoding="utf-8"))
        cases = []

        closed = deepcopy(base)
        closed["license"]["id"] = "DDB-PROPRIETARY"
        for entry in closed["entries"]:
            entry["license"] = deepcopy(closed["license"])
        cases.append(closed)

        duplicate = deepcopy(base)
        duplicate["entries"][1]["id"] = duplicate["entries"][0]["id"]
        cases.append(duplicate)

        broken = deepcopy(base)
        broken["entries"][0]["provenance"]["document_sha256"] = "0" * 64
        cases.append(broken)

        spoofed = deepcopy(base)
        spoofed["source"]["url"] = "https://www.dndbeyond.com/srd.evil.example"
        for entry in spoofed["entries"]:
            entry["source"] = deepcopy(spoofed["source"])
        cases.append(spoofed)

        wrong_digest = deepcopy(base)
        wrong_digest["source"]["document_sha256"] = "0" * 64
        for entry in wrong_digest["entries"]:
            entry["source"] = deepcopy(wrong_digest["source"])
            entry["provenance"]["document_sha256"] = "0" * 64
        cases.append(wrong_digest)

        for index, value in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / "srd-5.2.1"
                target.mkdir()
                (target / "catalog.json").write_text(
                    json.dumps(value), encoding="utf-8"
                )
                with self.assertRaises(CatalogValidationError):
                    RulesCatalog(root).load("srd-5.2.1")

    def test_rejects_invalid_entity_schema_id_and_dangling_reference(self):
        base = json.loads(BUNDLED_CATALOG.read_text(encoding="utf-8"))
        cases = []

        invalid_level = deepcopy(base)
        invalid_level["entries"][3]["data"]["level"] = 10
        cases.append(invalid_level)

        mismatched_id = deepcopy(base)
        mismatched_id["entries"][0]["id"] = "spell:fighter"
        cases.append(mismatched_id)

        dangling = deepcopy(base)
        dangling["entries"][0]["data"]["starting_feature_ids"] = [
            "feature:not-present"
        ]
        cases.append(dangling)

        float_hit_die = deepcopy(base)
        float_hit_die["entries"][0]["data"]["hit_die"] = 10.0
        cases.append(float_hit_die)

        infinite_weight = deepcopy(base)
        infinite_weight["entries"][5]["data"]["weight_lb"] = float("inf")
        cases.append(infinite_weight)

        changed_name = deepcopy(base)
        changed_name["entries"][0]["name"] = "Closed Compendium Text"
        cases.append(changed_name)

        changed_section = deepcopy(base)
        changed_section["entries"][0]["provenance"]["section"] = "Wrong Section"
        cases.append(changed_section)

        changed_page = deepcopy(base)
        changed_page["entries"][0]["provenance"]["page_labels"] = ["999"]
        cases.append(changed_page)

        for index, value in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / "srd-5.2.1"
                target.mkdir()
                (target / "catalog.json").write_text(
                    json.dumps(value), encoding="utf-8"
                )
                with self.assertRaises(CatalogValidationError):
                    RulesCatalog(root).load("srd-5.2.1")

    def test_rejects_oversized_catalog_before_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "srd-5.2.1"
            target.mkdir()
            catalog_path = target / "catalog.json"
            with catalog_path.open("wb") as catalog_file:
                catalog_file.truncate(MAX_CATALOG_BYTES + 1)
            with self.assertRaisesRegex(CatalogValidationError, "boyut"):
                RulesCatalog(root).load("srd-5.2.1")


if __name__ == "__main__":
    unittest.main()
