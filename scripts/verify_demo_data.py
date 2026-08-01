from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from seed_demo import load_metadata  # noqa: E402

from document_enrichment.config import Settings  # noqa: E402

ENTITY_QUERY = """
query GetEntity($urn: String!) {
  entity(urn: $urn) {
    urn
    ... on Dataset {
      properties { description }
      schemaMetadata { fields { fieldPath } }
      ownership {
        owners {
          owner {
            ... on CorpUser { urn }
            ... on CorpGroup { urn }
          }
        }
      }
    }
  }
}
"""


async def fetch_entities(urns: list[str], settings: Settings) -> list[dict]:
    """Fetch exactly the fixed demo URNs, bypassing unrelated search-index entries."""
    headers = {"Content-Type": "application/json"}
    if settings.datahub_token:
        headers["Authorization"] = f"Bearer {settings.datahub_token}"

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        async def fetch(urn: str) -> dict | None:
            try:
                response = await client.post(
                    str(settings.datahub_graphql_url),
                    json={"query": ENTITY_QUERY, "variables": {"urn": urn}},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RuntimeError(f"DataHub GraphQL request failed: {type(exc).__name__}") from exc
            if payload.get("errors"):
                raise RuntimeError(f"DataHub GraphQL returned errors: {payload['errors']}")
            entity = payload.get("data", {}).get("entity")
            return entity if isinstance(entity, dict) else None

        return [entity for entity in await asyncio.gather(*(fetch(urn) for urn in urns)) if entity]


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
    settings = Settings()
    expected_dataset_urns = {
        f"urn:li:dataset:(urn:li:dataPlatform:{fixture['platform']},{item['name']},{fixture['env']})"
        for item in fixture["datasets"]
    }
    expected_dataset_owners = {
        f"urn:li:dataset:(urn:li:dataPlatform:{fixture['platform']},{item['name']},{fixture['env']})": f"urn:li:corpuser:{item['owner']}"
        for item in fixture["datasets"]
    }
    domains, tags, teams, datasets = await asyncio.gather(
        fetch_entities([f"urn:li:domain:{item['id']}" for item in fixture["domains"]], settings),
        fetch_entities([f"urn:li:tag:{item['id']}" for item in fixture["tags"]], settings),
        fetch_entities([f"urn:li:corpGroup:{item['id']}" for item in fixture["teams"]], settings),
        fetch_entities(sorted(expected_dataset_urns), settings),
    )
    actual = {item["urn"]: item for item in datasets}
    missing = expected_dataset_urns - actual.keys()
    if missing:
        raise RuntimeError(f"Missing seeded datasets: {sorted(missing)}")
    incomplete = [
        item["urn"]
        for item in actual.values()
        if item["urn"] in expected_dataset_urns
        and (
            not item.get("properties", {}).get("description")
            or not item.get("schemaMetadata", {}).get("fields")
            or not item.get("ownership", {}).get("owners")
        )
    ]
    if incomplete:
        raise RuntimeError(f"Seeded datasets missing description, schema, or owner: {incomplete}")
    wrong_owners = [
        item["urn"]
        for item in actual.values()
        if item["urn"] in expected_dataset_urns
        and expected_dataset_owners[item["urn"]]
        not in {
            owner["owner"]["urn"]
            for owner in item.get("ownership", {}).get("owners", [])
            if isinstance(owner.get("owner"), dict) and owner["owner"].get("urn")
        }
    ]
    if wrong_owners:
        raise RuntimeError(f"Seeded datasets have an unexpected technical owner: {wrong_owners}")
    entity_groups = {"domains": domains, "tags": tags, "teams": teams}
    for key, prefix in [("domains", "urn:li:domain:"), ("tags", "urn:li:tag:"), ("teams", "urn:li:corpGroup:")]:
        names = {item["id"] for item in fixture[key]}
        present = {item["urn"].removeprefix(prefix) for item in entity_groups[key]}
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
