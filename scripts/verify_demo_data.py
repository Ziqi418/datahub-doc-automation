from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from document_enrichment.config import Settings
from document_enrichment.datahub.catalog import GraphQLDataHubCatalogGateway
from seed_demo import load_metadata


def validate_gold_fixtures() -> None:
    fixture = load_metadata()
    valid = {
        "domain": {item["id"] for item in fixture["domains"]},
        "tags": {item["id"] for item in fixture["tags"]},
        "owner": {item["id"] for item in fixture["teams"]},
        "datasets": {item["name"] for item in fixture["datasets"]},
    }
    gold_files = sorted((ROOT / "demo" / "gold").glob("*.yaml"))
    if len(gold_files) != 8:
        raise RuntimeError(f"Expected 8 gold files, got {len(gold_files)}")
    for path in gold_files:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        expected = payload.get("expected", {}) if isinstance(payload, dict) else {}
        document = ROOT / "demo" / "documents" / str(payload.get("document", ""))
        if not document.is_file():
            raise RuntimeError(f"{path.name} references a missing document")
        if expected.get("domain") not in valid["domain"]:
            raise RuntimeError(f"{path.name} has an unknown expected domain")
        if expected.get("owner") not in valid["owner"]:
            raise RuntimeError(f"{path.name} has an unknown expected owner")
        for kind in ("tags", "datasets"):
            values = expected.get(kind)
            if not isinstance(values, list) or not values or not set(values).issubset(valid[kind]):
                raise RuntimeError(f"{path.name} has invalid expected {kind}")


async def verify_live() -> None:
    fixture = load_metadata()
    gateway = GraphQLDataHubCatalogGateway(Settings())
    try:
        snapshot = await gateway.get_snapshot(force_refresh=True)
    finally:
        await gateway.aclose()
    expected_dataset_urns = {
        f"urn:li:dataset:(urn:li:dataPlatform:{fixture['platform']},{item['name']},{fixture['env']})"
        for item in fixture["datasets"]
    }
    actual = {item.urn: item for item in snapshot.datasets}
    missing = expected_dataset_urns - actual.keys()
    if missing:
        raise RuntimeError(f"Missing seeded datasets: {sorted(missing)}")
    incomplete = [item.urn for item in actual.values() if item.urn in expected_dataset_urns and (not item.description or not item.schema_fields or not item.owner_urns)]
    if incomplete:
        raise RuntimeError(f"Seeded datasets missing description, schema, or owner: {incomplete}")
    for key, prefix, expected in [("domains", "urn:li:domain:", 3), ("tags", "urn:li:tag:", 7), ("teams", "urn:li:corpgroup:", 3)]:
        names = {item["id"] for item in fixture[key]}
        present = {item.urn.removeprefix(prefix) for item in (snapshot.owners if key == "teams" else getattr(snapshot, key))}
        absent = names - present
        if absent:
            raise RuntimeError(f"Missing seeded {key}: {sorted(absent)}")
    print("Live DataHub demo data is complete.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="Validate local seed fixture only")
    args = parser.parse_args()
    if args.offline:
        load_metadata()
        validate_gold_fixtures()
        print("Local demo metadata and 8 gold fixtures are valid.")
        return 0
    asyncio.run(verify_live())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
