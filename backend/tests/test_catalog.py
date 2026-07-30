from __future__ import annotations

import httpx
import pytest

from document_enrichment.config import Settings
from document_enrichment.datahub.catalog import (
    CatalogUnavailableError,
    GraphQLDataHubCatalogGateway,
)


@pytest.mark.asyncio
async def test_graphql_catalog_paginates_and_caches(tmp_path) -> None:
    calls: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        input_ = payload["variables"]["input"]
        entity_type, start = input_["type"], input_["start"]
        calls.append((entity_type, start))
        entity = {"urn": f"urn:li:{entity_type.lower()}:{start}", "type": entity_type}
        if entity_type == "DOMAIN":
            entity["properties"] = {"name": f"domain-{start}", "description": "d"}
        elif entity_type == "TAG":
            entity["properties"] = {"name": f"tag-{start}", "description": "t"}
        elif entity_type in {"CORP_USER", "CORP_GROUP"}:
            entity.update({"username": f"owner-{start}", "properties": {"displayName": f"owner-{start}"}})
        else:
            entity.update({"name": f"dataset-{start}", "properties": {"name": f"dataset-{start}", "description": "x"}, "schemaMetadata": {"fields": []}, "ownership": {"owners": []}, "domains": {"domains": []}, "globalTags": {"tags": []}})
        # Dataset requires two pages; every other entity type fits in one.
        total = 101 if entity_type == "DATASET" else 1
        rows = [{"entity": entity}] if start in (0, 100) or entity_type != "DATASET" else []
        return httpx.Response(200, json={"data": {"search": {"total": total, "searchResults": rows}}})

    settings = Settings(database_path=tmp_path / "test.db", catalog_cache_ttl_seconds=300)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = GraphQLDataHubCatalogGateway(settings, client)
    first = await gateway.get_snapshot()
    second = await gateway.get_snapshot()
    assert len(first.datasets) == 2
    assert second == first
    assert ("DATASET", 100) in calls
    assert calls.count(("DATASET", 0)) == 1


@pytest.mark.asyncio
async def test_graphql_errors_are_not_mapped_to_empty_catalog(tmp_path) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"errors": [{"message": "down"}]})))
    gateway = GraphQLDataHubCatalogGateway(Settings(database_path=tmp_path / "test.db"), client)
    with pytest.raises(CatalogUnavailableError):
        await gateway.get_snapshot()
