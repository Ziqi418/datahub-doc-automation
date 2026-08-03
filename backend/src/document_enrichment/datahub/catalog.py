from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from typing import Any, Protocol

import httpx

from document_enrichment.config import Settings
from document_enrichment.models import CatalogSnapshot, Dataset, Domain, Owner, Tag


class CatalogUnavailableError(RuntimeError):
    """The catalog could not be obtained from DataHub."""


class _MalformedSearchResultError(CatalogUnavailableError):
    """DataHub returned a null entity for one otherwise valid search page."""


LOGGER = logging.getLogger(__name__)


class DataHubCatalogGateway(Protocol):
    async def get_snapshot(self, *, force_refresh: bool = False) -> CatalogSnapshot: ...

    async def search(self, entity_type: str, query: str, limit: int = 20) -> list[object]: ...


_SEARCH_QUERY = """
query SearchCatalog($input: SearchInput!) {
  search(input: $input) {
    total
    searchResults {
      entity {
        urn
        type
        ... on Dataset {
          name
          properties { name description qualifiedName }
          schemaMetadata { fields { fieldPath } }
          ownership {
            owners {
              owner {
                ... on CorpUser { urn }
                ... on CorpGroup { urn }
              }
            }
          }
          domain { domain { urn } }
          tags { tags { tag { urn } } }
          deprecation { deprecated }
        }
        ... on Domain { properties { name description } }
        ... on Tag { properties { name description } }
        ... on CorpUser { username properties { displayName title } }
        ... on CorpGroup { name properties { displayName description } }
      }
    }
  }
}
"""


class GraphQLDataHubCatalogGateway:
    """Read-only, paginated DataHub GraphQL catalog adapter.

    DataHub's GraphQL schema returns a polymorphic `entity`; mapping stays defensive
    so optional aspects absent from an entity never turn into a fake empty catalog.
    """

    PAGE_SIZE = 100

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.http_timeout_seconds)
        self._owns_client = client is None
        self._cache: CatalogSnapshot | None = None
        self._cached_at = 0.0
        self._refresh_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_snapshot(self, *, force_refresh: bool = False) -> CatalogSnapshot:
        # A forced refresh must bypass a snapshot that existed before the caller
        # arrived, but concurrent callers can share a snapshot refreshed while
        # they waited for the lock.
        requested_at = time.monotonic()
        if not force_refresh and self._fresh_cache():
            return self._cache_or_raise()
        async with self._refresh_lock:
            if self._fresh_cache() and (not force_refresh or self._cached_at >= requested_at):
                return self._cache_or_raise()
            snapshot = CatalogSnapshot(
                domains=[self._to_domain(row) for row in await self._all("DOMAIN")],
                tags=[self._to_tag(row) for row in await self._all("TAG")],
                owners=[self._to_owner(row) for row in await self._all("CORP_USER", "CORP_GROUP")],
                datasets=[self._to_dataset(row) for row in await self._all("DATASET")],
            )
            self._cache = snapshot
            self._cached_at = time.monotonic()
            return snapshot

    async def search(self, entity_type: str, query: str, limit: int = 20) -> list[object]:
        snapshot = await self.get_snapshot()
        collections: dict[str, Iterable[object]] = {
            "domains": snapshot.domains,
            "tags": snapshot.tags,
            "owners": snapshot.owners,
            "datasets": snapshot.datasets,
        }
        if entity_type not in collections:
            raise ValueError(f"Unsupported catalog entity type: {entity_type}")
        normalized = query.casefold().strip()
        items = [
            item
            for item in collections[entity_type]
            if not normalized
            or normalized in item.name.casefold()
            or normalized in item.description.casefold()
            or normalized in item.urn.casefold()
            or (isinstance(item, Dataset) and normalized in item.qualified_name.casefold())
        ]
        return sorted(items, key=lambda item: (item.name.casefold(), item.urn))[:limit]

    def _fresh_cache(self) -> bool:
        return self._cache is not None and (
            time.monotonic() - self._cached_at < self._settings.catalog_cache_ttl_seconds
        )

    def _cache_or_raise(self) -> CatalogSnapshot:
        if self._cache is None:
            raise CatalogUnavailableError("catalog cache unexpectedly empty")
        return self._cache

    async def _all(self, *entity_types: str) -> list[dict[str, Any]]:
        # DataHub search accepts one entity type per request. Keep the merge stable.
        merged: list[dict[str, Any]] = []
        for entity_type in entity_types:
            start = 0
            page_size = 10 if entity_type == "CORP_GROUP" else self.PAGE_SIZE
            while True:
                result = await self._search_page(entity_type, start, page_size)
                if not isinstance(result, dict):
                    raise CatalogUnavailableError("DataHub GraphQL search returned no result")
                rows = result.get("searchResults") or []
                if not isinstance(rows, list):
                    raise CatalogUnavailableError("DataHub GraphQL search returned invalid rows")
                merged.extend(row["entity"] for row in rows if isinstance(row.get("entity"), dict))
                # `start` is an offset into the search result set, not the number
                # of entity nodes successfully mapped. Advance by requested page
                # size so a partial page cannot cause duplicates or an infinite loop.
                start += page_size
                if not rows or start >= int(result.get("total") or 0):
                    break
        return merged

    async def _search_page(self, entity_type: str, start: int, count: int) -> dict[str, Any] | None:
        variables = {
            "input": {"type": entity_type, "query": "*", "start": start, "count": count}
        }
        try:
            payload = await self._execute(variables)
        except _MalformedSearchResultError:
            if entity_type != "CORP_GROUP":
                raise
            return await self._recover_corp_group_page(start, count)
        result = payload.get("data", {}).get("search")
        return result if isinstance(result, dict) else None

    async def _recover_corp_group_page(self, start: int, count: int) -> dict[str, Any]:
        """Binary-split only malformed CorpGroup pages, retaining valid groups."""
        if count == 1:
            LOGGER.warning("Skipping malformed DataHub CorpGroup search result at offset %s", start)
            return {"total": start + 1, "searchResults": []}
        left_count = count // 2
        left = await self._search_page("CORP_GROUP", start, left_count)
        right = await self._search_page("CORP_GROUP", start + left_count, count - left_count)
        assert left is not None and right is not None
        return {
            "total": max(int(left.get("total") or 0), int(right.get("total") or 0)),
            "searchResults": [*(left.get("searchResults") or []), *(right.get("searchResults") or [])],
        }

    async def _execute(self, variables: dict[str, Any]) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._settings.datahub_token:
            headers["Authorization"] = f"Bearer {self._settings.datahub_token}"
        try:
            response = await self._client.post(
                str(self._settings.datahub_graphql_url),
                json={"query": _SEARCH_QUERY, "variables": variables},
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CatalogUnavailableError(
                f"DataHub GraphQL request failed: {type(exc).__name__}"
            ) from exc
        if body.get("errors"):
            errors = body["errors"]
            if all(
                isinstance(error, dict)
                and error.get("extensions", {}).get("classification")
                == "NullValueInNonNullableField"
                for error in errors
            ):
                raise _MalformedSearchResultError("DataHub GraphQL returned malformed search entities")
            raise CatalogUnavailableError("DataHub GraphQL returned errors")
        return body

    @staticmethod
    def _properties(row: dict[str, Any]) -> dict[str, Any]:
        return row.get("properties") if isinstance(row.get("properties"), dict) else {}

    def _to_domain(self, row: dict[str, Any]) -> Domain:
        props = self._properties(row)
        return Domain(
            urn=row["urn"],
            name=props.get("name") or row["urn"],
            description=props.get("description") or "",
        )

    def _to_tag(self, row: dict[str, Any]) -> Tag:
        props = self._properties(row)
        return Tag(
            urn=row["urn"],
            name=props.get("name") or row["urn"],
            description=props.get("description") or "",
        )

    def _to_owner(self, row: dict[str, Any]) -> Owner:
        props = self._properties(row)
        owner_type = row.get("type") or "CORP_USER"
        name = props.get("displayName") or row.get("username") or row.get("name") or row["urn"]
        return Owner(
            urn=row["urn"],
            name=name,
            description=props.get("description") or "",
            owner_type=owner_type,
            title=props.get("title") or "",
        )

    def _to_dataset(self, row: dict[str, Any]) -> Dataset:
        props = self._properties(row)
        schema = row.get("schemaMetadata") if isinstance(row.get("schemaMetadata"), dict) else {}
        ownership = row.get("ownership") if isinstance(row.get("ownership"), dict) else {}
        domain = row.get("domain") if isinstance(row.get("domain"), dict) else {}
        tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
        deprecation = row.get("deprecation") if isinstance(row.get("deprecation"), dict) else {}
        return Dataset(
            urn=row["urn"],
            name=props.get("name") or row.get("name") or row["urn"],
            qualified_name=props.get("qualifiedName") or row.get("name") or row["urn"],
            description=props.get("description") or "",
            schema_fields=[
                field.get("fieldPath", "")
                for field in schema.get("fields", [])
                if field.get("fieldPath")
            ][:100],
            owner_urns=[
                owner["owner"]["urn"]
                for owner in ownership.get("owners", [])
                if owner.get("owner", {}).get("urn")
            ],
            domain_urn=domain.get("domain", {}).get("urn"),
            tag_urns=[
                tag["tag"]["urn"] for tag in tags.get("tags", []) if tag.get("tag", {}).get("urn")
            ],
            deprecated=bool(deprecation.get("deprecated", False)),
        )
