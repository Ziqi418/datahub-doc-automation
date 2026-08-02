"""Evaluate deterministic Dataset retrieval against the local gold fixtures."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from document_enrichment.models import CatalogSnapshot, Dataset  # noqa: E402
from document_enrichment.recommendation.rules import dataset_candidates  # noqa: E402


def build_catalog(metadata: dict) -> CatalogSnapshot:
    platform_urn = f"urn:li:dataPlatform:{metadata['platform']}"
    return CatalogSnapshot(
        datasets=[
            Dataset(
                urn=f"urn:li:dataset:({platform_urn},{item['name']},{metadata['env']})",
                name=item["name"],
                qualified_name=item["name"],
                description=item["description"],
                schema_fields=item["fields"],
                owner_urns=[f"urn:li:corpuser:{item['owner']}"],
                domain_urn=f"urn:li:domain:{item['domain']}",
                tag_urns=[f"urn:li:tag:{tag}" for tag in item["tags"]],
            )
            for item in metadata["datasets"]
        ]
    )


def main() -> int:
    metadata = json.loads((ROOT / "demo" / "metadata" / "jaffle_shop.json").read_text(encoding="utf-8"))
    catalog = build_catalog(metadata)
    rows: list[dict[str, object]] = []
    for gold_path in sorted((ROOT / "demo" / "gold").glob("*.yaml")):
        gold = yaml.safe_load(gold_path.read_text(encoding="utf-8"))
        document = ROOT / "demo" / "documents" / gold["document"]
        expected = set(gold["expected"]["datasets"])
        candidates = dataset_candidates(document.read_text(encoding="utf-8"), document.name, catalog, limit=30)
        predicted = [item.urn.split(",", maxsplit=1)[1].rsplit(",", maxsplit=1)[0] for item in candidates]
        rows.append(
            {
                "document": document.name,
                "expected": sorted(expected),
                "candidates": predicted,
                "missing": sorted(expected - set(predicted)),
            }
        )
    expected_count = sum(len(row["expected"]) for row in rows)
    retrieved_count = sum(len(row["expected"]) - len(row["missing"]) for row in rows)
    passed = all(not row["missing"] for row in rows)
    print(
        json.dumps(
            {
                "dataset_recall_at_30": retrieved_count / expected_count,
                "documents": rows,
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
