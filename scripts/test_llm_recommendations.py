"""Run one real Gate 4 recommendation against the demo fixtures.

Requires LLM_API_KEY and LLM_MODEL in the repository-root .env file. The
script intentionally uses fixture metadata only; it does not read or write
DataHub.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from document_enrichment.config import Settings  # noqa: E402
from document_enrichment.models import CatalogSnapshot, Dataset, Domain, Owner, Tag  # noqa: E402
from document_enrichment.recommendation.llm import OpenAICompatibleProvider, recommend_with_llm  # noqa: E402
from document_enrichment.recommendation.rules import recommend_rules  # noqa: E402


def build_catalog(metadata: dict[str, object]) -> CatalogSnapshot:
    platform_urn = f"urn:li:dataPlatform:{metadata['platform']}"
    environment = metadata["env"]
    return CatalogSnapshot(
        domains=[
            Domain(urn=f"urn:li:domain:{item['id']}", name=item["name"], description=item["description"])
            for item in metadata["domains"]
        ],
        tags=[
            Tag(urn=f"urn:li:tag:{item['id']}", name=item["id"], description=item["description"])
            for item in metadata["tags"]
        ],
        owners=[
            Owner(
                urn=f"urn:li:corpuser:{item['id']}",
                name=item["name"],
                title=item["description"],
                owner_type="CORP_USER",
            )
            for item in metadata["teams"]
        ],
        datasets=[
            Dataset(
                urn=f"urn:li:dataset:({platform_urn},{item['name']},{environment})",
                name=item["name"],
                qualified_name=item["name"],
                description=item["description"],
                schema_fields=item["fields"],
                owner_urns=[f"urn:li:corpuser:{item['owner']}"],
                domain_urn=f"urn:li:domain:{item['domain']}",
                tag_urns=[f"urn:li:tag:{tag}" for tag in item["tags"]],
            )
            for item in metadata["datasets"]
        ],
    )


async def main() -> int:
    settings = Settings()
    if not settings.llm_api_key or not settings.llm_model:
        print("LLM_API_KEY and LLM_MODEL must be set in .env", file=sys.stderr)
        return 2

    metadata = json.loads((ROOT / "demo" / "metadata" / "jaffle_shop.json").read_text(encoding="utf-8"))
    document_path = ROOT / "demo" / "documents" / "daily-sales-dashboard.md"
    text = document_path.read_text(encoding="utf-8")
    catalog = build_catalog(metadata)
    rules = recommend_rules(text, document_path.name, catalog)
    provider = OpenAICompatibleProvider(settings)
    try:
        result = await recommend_with_llm(
            provider=provider, text=text, catalog=catalog, rule_recommendations=rules
        )
    finally:
        await provider.aclose()

    print(
        json.dumps(
            {
                "provider": result.provider,
                "elapsed_ms": result.elapsed_ms,
                "domain": result.domain.urn if result.domain else None,
                "tags": [item.urn for item in result.tags],
                "owner": result.owner.urn if result.owner else None,
                "datasets": [item.urn for item in result.datasets],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
