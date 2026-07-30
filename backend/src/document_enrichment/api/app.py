from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from document_enrichment.config import Settings, get_settings
from document_enrichment.datahub.catalog import (
    CatalogUnavailableError,
    DataHubCatalogGateway,
    GraphQLDataHubCatalogGateway,
)
from document_enrichment.db.store import (
    AnalysisNotFoundError,
    InvalidStateError,
    SQLiteAnalysisStore,
)
from document_enrichment.models import (
    AnalysisRecord,
    AnalysisStatus,
    CatalogSearchItem,
    CatalogSearchResponse,
    EntityType,
    RecommendationSet,
    ReviewAction,
    ReviewSelection,
    UploadResponse,
)
from document_enrichment.recommendation.llm import (
    LLMProvider,
    OpenAICompatibleProvider,
    ProviderError,
    recommend_with_llm,
)
from document_enrichment.recommendation.rules import recommend_rules

LOGGER = logging.getLogger(__name__)


class ReviewResponse(BaseModel):
    analysis: AnalysisRecord
    actions: list[ReviewAction]


def get_store(request: Request) -> SQLiteAnalysisStore:
    return request.app.state.store


def get_gateway(request: Request) -> DataHubCatalogGateway:
    return request.app.state.catalog_gateway


def get_llm_provider(request: Request) -> LLMProvider:
    provider = request.app.state.llm_provider
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM is not configured; set LLM_API_KEY and LLM_MODEL",
        )
    return provider


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = SQLiteAnalysisStore(app_settings.database_path)
        store.initialize()
        gateway = GraphQLDataHubCatalogGateway(app_settings)
        app.state.store = store
        app.state.catalog_gateway = gateway
        try:
            app.state.llm_provider = OpenAICompatibleProvider(app_settings)
        except ProviderError:
            app.state.llm_provider = None
        yield
        await gateway.aclose()
        provider = app.state.llm_provider
        if provider and hasattr(provider, "aclose"):
            await provider.aclose()

    app = FastAPI(title="DataHub Document Enrichment API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/ready")
    async def ready() -> dict[str, str]:
        try:
            await get_gateway_from_app(app).get_snapshot()
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=503, detail="DataHub catalog unavailable") from exc
        return {"status": "ok", "database": "ok", "datahub": "ok"}

    @app.post("/api/analyses", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
    async def upload_analysis(
        file: Annotated[UploadFile, File(...)],
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
    ) -> UploadResponse:
        filename = Path(file.filename or "").name
        extension = Path(filename).suffix.casefold()
        if extension not in {".md", ".txt"}:
            raise HTTPException(status_code=415, detail="Only .md and .txt files are supported")
        raw = await file.read(app_settings.max_upload_bytes + 1)
        if len(raw) > app_settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="File exceeds 256 KiB limit")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="File must be valid UTF-8") from exc
        if not content.strip():
            raise HTTPException(status_code=400, detail="File must not be empty")
        if len(content) > app_settings.max_document_characters:
            raise HTTPException(status_code=413, detail="Document exceeds 30,000 character limit")
        record = store.create(
            analysis_id=str(uuid4()),
            filename=filename,
            content=content,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        return UploadResponse(analysis=record)

    @app.post("/api/analyses/{analysis_id}/recommend", response_model=AnalysisRecord)
    async def recommend_analysis(
        analysis_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
        provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    ) -> AnalysisRecord:
        try:
            record = store.transition(analysis_id, AnalysisStatus.ANALYZING)
            content = store.content(analysis_id)
            snapshot = await gateway.get_snapshot()
            rules = recommend_rules(content, record.source_filename, snapshot)
            recommendations = await recommend_with_llm(
                provider=provider, text=content, catalog=snapshot, rule_recommendations=rules
            )
            return store.save_recommendations(analysis_id, recommendations)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (CatalogUnavailableError, ProviderError) as exc:
            # An explicit failure state permits a safe retry without any DataHub write.
            try:
                store.transition(analysis_id, AnalysisStatus.ANALYSIS_FAILED, error_code=type(exc).__name__)
            except (AnalysisNotFoundError, InvalidStateError):
                pass
            LOGGER.warning("recommendation failed for analysis %s: %s", analysis_id, type(exc).__name__)
            raise HTTPException(status_code=503, detail="Catalog or LLM recommendation unavailable") from exc

    @app.get("/api/analyses/{analysis_id}", response_model=AnalysisRecord)
    async def get_analysis(
        analysis_id: str, store: Annotated[SQLiteAnalysisStore, Depends(get_store)]
    ) -> AnalysisRecord:
        try:
            return store.get(analysis_id)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc

    @app.put("/api/analyses/{analysis_id}/review", response_model=ReviewResponse)
    async def review_analysis(
        analysis_id: str,
        selection: ReviewSelection,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> ReviewResponse:
        try:
            record = store.get(analysis_id)
            snapshot = await gateway.get_snapshot()
            _validate_selection(selection, snapshot)
            actions = _review_actions(record.recommendations or RecommendationSet(), selection)
            updated = store.save_review(analysis_id, selection, actions)
            return ReviewResponse(analysis=updated, actions=store.review_actions(analysis_id))
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=503, detail="DataHub catalog unavailable") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/catalog/{entity_type}", response_model=CatalogSearchResponse)
    async def search_catalog(
        entity_type: str,
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
        q: str = "",
        limit: int = Query(default=20, ge=1, le=20),
    ) -> CatalogSearchResponse:
        if entity_type not in {"domains", "tags", "owners", "datasets"}:
            raise HTTPException(status_code=404, detail="Unknown catalog entity type")
        try:
            items = await gateway.search(entity_type, q, limit)
            return CatalogSearchResponse(items=[_catalog_item(item) for item in items], limit=limit)
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=503, detail="DataHub catalog unavailable") from exc

    @app.post("/api/catalog/refresh", response_model=dict[str, int])
    async def refresh_catalog(
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> dict[str, int]:
        try:
            snapshot = await gateway.get_snapshot(force_refresh=True)
            return {"domains": len(snapshot.domains), "tags": len(snapshot.tags), "owners": len(snapshot.owners), "datasets": len(snapshot.datasets)}
        except CatalogUnavailableError as exc:
            raise HTTPException(status_code=503, detail="DataHub catalog unavailable") from exc

    return app


def get_gateway_from_app(app: FastAPI) -> DataHubCatalogGateway:
    return app.state.catalog_gateway


def _validate_selection(selection: ReviewSelection, snapshot) -> None:
    entity_sets = {
        "domain": {item.urn for item in snapshot.domains},
        "tag": {item.urn for item in snapshot.tags},
        "owner": {item.urn for item in snapshot.owners},
        "dataset": {item.urn for item in snapshot.datasets},
    }
    expected = [
        ("domain", selection.domain_urn),
        ("owner", selection.owner_urn),
        *[("tag", urn) for urn in selection.tag_urns],
        *[("dataset", urn) for urn in selection.dataset_urns],
    ]
    for entity_type, urn in expected:
        if urn is not None and urn not in entity_sets[entity_type]:
            raise ValueError(f"{urn} is not an existing {entity_type}")


def _catalog_item(item: object) -> CatalogSearchItem:
    return CatalogSearchItem(
        urn=item.urn,
        name=item.name,
        description=item.description,
        qualified_name=getattr(item, "qualified_name", None),
        owner_type=getattr(item, "owner_type", None),
        title=getattr(item, "title", None),
    )


def _review_actions(recommendations: RecommendationSet, selection: ReviewSelection) -> list[ReviewAction]:
    now = datetime.now(UTC)
    pairs = [
        (EntityType.DOMAIN, [recommendations.domain.urn] if recommendations.domain else [], [selection.domain_urn] if selection.domain_urn else []),
        (EntityType.TAG, [item.urn for item in recommendations.tags], selection.tag_urns),
        (EntityType.OWNER, [recommendations.owner.urn] if recommendations.owner else [], [selection.owner_urn] if selection.owner_urn else []),
        (EntityType.DATASET, [item.urn for item in recommendations.datasets], selection.dataset_urns),
    ]
    actions: list[ReviewAction] = []
    for entity_type, recommended, selected in pairs:
        recommended_set, selected_set = set(recommended), set(selected)
        for urn in sorted(recommended_set & selected_set):
            actions.append(ReviewAction(entity_type=entity_type, urn=urn, action="accepted", created_at=now))
        additions = sorted(selected_set - recommended_set)
        removed = sorted(recommended_set - selected_set)
        for urn in additions:
            actions.append(
                ReviewAction(
                    entity_type=entity_type,
                    urn=urn,
                    action="replaced" if removed else "accepted",
                    replaced_urn=removed.pop(0) if removed else None,
                    created_at=now,
                )
            )
        for urn in removed:
            actions.append(ReviewAction(entity_type=entity_type, urn=urn, action="removed", created_at=now))
    return actions


app = create_app()
