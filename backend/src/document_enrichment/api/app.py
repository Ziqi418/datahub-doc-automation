from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from document_enrichment.config import Settings, get_settings
from document_enrichment.datahub.catalog import (
    CatalogUnavailableError,
    DataHubCatalogGateway,
    GraphQLDataHubCatalogGateway,
)
from document_enrichment.models import (
    CatalogRefreshResponse,
    CatalogSearchItem,
    CatalogSearchResponse,
    Dataset,
    Owner,
)

CatalogEntityType = Literal["domains", "tags", "owners", "datasets"]


def create_app(
    settings: Settings | None = None,
    catalog_gateway: DataHubCatalogGateway | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    gateway = catalog_gateway or GraphQLDataHubCatalogGateway(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        close = getattr(gateway, "aclose", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    app = FastAPI(title="DataHub Document Enrichment API", lifespan=lifespan)
    app.state.catalog_gateway = gateway
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(CatalogUnavailableError)
    async def catalog_unavailable(_: Request, __: CatalogUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "DataHub catalog unavailable"})

    @app.get("/api/catalog/{entity_type}", response_model=CatalogSearchResponse)
    async def search_catalog(
        entity_type: CatalogEntityType,
        q: Annotated[str, Query(max_length=200)] = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> CatalogSearchResponse:
        items = await gateway.search(entity_type, q, limit)
        return CatalogSearchResponse(
            items=[_catalog_item(item) for item in items],
            limit=limit,
        )

    @app.post("/api/catalog/refresh", response_model=CatalogRefreshResponse)
    async def refresh_catalog() -> CatalogRefreshResponse:
        snapshot = await gateway.get_snapshot(force_refresh=True)
        return CatalogRefreshResponse(
            domains=len(snapshot.domains),
            tags=len(snapshot.tags),
            owners=len(snapshot.owners),
            datasets=len(snapshot.datasets),
        )

    return app


def _catalog_item(item: object) -> CatalogSearchItem:
    """Expose only the display fields required by the replacement picker."""
    return CatalogSearchItem(
        urn=item.urn,
        name=item.name,
        description=item.description,
        qualified_name=item.qualified_name if isinstance(item, Dataset) else None,
        owner_type=item.owner_type if isinstance(item, Owner) else None,
        title=item.title if isinstance(item, Owner) else None,
    )


app = create_app()
