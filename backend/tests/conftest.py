from __future__ import annotations

import json
from pathlib import Path

import pytest

from document_enrichment.models import CatalogSnapshot, Dataset, Domain, Owner, Tag


@pytest.fixture
def catalog() -> CatalogSnapshot:
    root = Path(__file__).resolve().parents[2]
    data = json.loads((root / "demo" / "metadata" / "jaffle_shop.json").read_text())
    platform = data["platform"]
    env = data["env"]
    return CatalogSnapshot(
        domains=[
            Domain(
                urn=f"urn:li:domain:{item['id']}",
                name=item["name"],
                description=item["description"],
            )
            for item in data["domains"]
        ],
        tags=[
            Tag(urn=f"urn:li:tag:{item['id']}", name=item["id"], description=item["description"])
            for item in data["tags"]
        ],
        owners=[
            Owner(
                urn=f"urn:li:corpgroup:{item['id']}",
                name=item["name"],
                description=item["description"],
                owner_type="CORP_GROUP",
            )
            for item in data["teams"]
        ],
        datasets=[
            Dataset(
                urn=f"urn:li:dataset:(urn:li:dataPlatform:{platform},{item['name']},{env})",
                name=item["name"],
                qualified_name=item["name"],
                description=item["description"],
                schema_fields=item["fields"],
                owner_urns=[f"urn:li:corpgroup:{item['owner']}"],
                domain_urn=f"urn:li:domain:{item['domain']}",
                tag_urns=[f"urn:li:tag:{tag}" for tag in item["tags"]],
            )
            for item in data["datasets"]
        ],
    )


class InMemoryCatalog:
    def __init__(self, catalog: CatalogSnapshot) -> None:
        self.catalog = catalog
        self.calls = 0

    async def get_snapshot(self, *, force_refresh: bool = False) -> CatalogSnapshot:
        self.calls += 1
        return self.catalog

    async def search(self, entity_type: str, query: str, limit: int = 20) -> list[object]:
        values = getattr(self.catalog, entity_type)
        return values[:limit]


@pytest.fixture
def in_memory_catalog(catalog: CatalogSnapshot) -> InMemoryCatalog:
    return InMemoryCatalog(catalog)
