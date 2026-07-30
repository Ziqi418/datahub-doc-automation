"""Read-only DataHub preflight. It never emits mutations."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from document_enrichment.config import Settings


async def main() -> int:
    settings = Settings()
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            health = await client.get(f"{str(settings.datahub_gms_url).rstrip('/')}/health")
            health.raise_for_status()
            schema = await client.post(
                str(settings.datahub_graphql_url),
                json={"query": "query { __type(name: \"Mutation\") { fields { name } } }"},
            )
            schema.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"DataHub preflight failed: {type(exc).__name__}", file=sys.stderr)
            return 1
    fields = {item["name"] for item in schema.json().get("data", {}).get("__type", {}).get("fields", [])}
    if "createDocument" not in fields:
        print("DataHub GraphQL Document API is unavailable", file=sys.stderr)
        return 1
    print("DataHub GMS healthy; GraphQL createDocument mutation is available.")
    print("ES_BULK_REFRESH_POLICY must be checked in the Quickstart GMS container (read-only preflight cannot inspect Docker).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
