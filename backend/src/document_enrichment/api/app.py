from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from document_enrichment.config import Settings, get_settings
from document_enrichment.datahub.catalog import (
    CatalogUnavailableError,
    DataHubCatalogGateway,
    GraphQLDataHubCatalogGateway,
)
from document_enrichment.datahub.conflicts import (
    ConflictGatewayUnavailableError,
    DocumentConflictGateway,
    GraphQLDocumentConflictGateway,
    detect_conflicts,
    title_from_content,
)
from document_enrichment.datahub.mock_changes import recent_database_changes
from document_enrichment.db.store import (
    AnalysisNotFoundError,
    InvalidStateError,
    SQLiteAnalysisStore,
    utcnow,
)
from document_enrichment.extraction.document import extract_document
from document_enrichment.models import (
    AnalysisListResponse,
    AnalysisRecord,
    AnalysisStatus,
    CatalogRefreshResponse,
    CatalogSearchItem,
    CatalogSearchResponse,
    ConflictReviewResponse,
    Dataset,
    DatasetCandidatesResponse,
    EntityType,
    FieldReviewResponse,
    FieldSuggestion,
    FreshnessCheckResponse,
    FreshnessDifference,
    Owner,
    PublishResponse,
    RecentChangeCheckResponse,
    RecommendationSet,
    ReviewAction,
    ReviewSelection,
    SchemaValidationResponse,
    UploadResponse,
)
from document_enrichment.publishing import (
    DataHubDocumentPublisher,
    DocumentPublisher,
    PublishError,
    PublishVerificationError,
)
from document_enrichment.recommendation.llm import (
    LLMProvider,
    OpenAICompatibleProvider,
    ProviderError,
    recommend_with_llm,
)
from document_enrichment.recommendation.rules import (
    dataset_candidates,
    merge_dataset_candidates,
    recommend_rules,
)
from document_enrichment.validation import lint_schema_references

LOGGER = logging.getLogger(__name__)
CatalogEntityType = Literal["domains", "tags", "owners", "datasets"]


class ReviewResponse(BaseModel):
    analysis: AnalysisRecord
    actions: list[ReviewAction]


def get_store(request: Request) -> SQLiteAnalysisStore:
    return request.app.state.store


def get_gateway(request: Request) -> DataHubCatalogGateway:
    return request.app.state.catalog_gateway


def get_llm_provider(request: Request) -> LLMProvider | None:
    provider = request.app.state.llm_provider
    if provider is None:
        if request.app.state.settings.demo_mode:
            return None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM is not configured; set LLM_API_KEY and LLM_MODEL",
        )
    return provider


def get_publisher(request: Request) -> DocumentPublisher:
    return request.app.state.publisher


def get_conflict_gateway(request: Request) -> DocumentConflictGateway:
    return request.app.state.conflict_gateway


def create_app(
    settings: Settings | None = None,
    catalog_gateway: DataHubCatalogGateway | None = None,
    conflict_gateway: DocumentConflictGateway | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    gateway = catalog_gateway or GraphQLDataHubCatalogGateway(app_settings)
    document_gateway = conflict_gateway or GraphQLDocumentConflictGateway(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        store = SQLiteAnalysisStore(app_settings.database_path)
        store.initialize()
        app.state.store = store
        app.state.catalog_gateway = gateway
        app.state.conflict_gateway = document_gateway
        app.state.publisher = DataHubDocumentPublisher(app_settings)
        try:
            app.state.llm_provider = OpenAICompatibleProvider(app_settings)
        except ProviderError:
            app.state.llm_provider = None
        yield
        close = getattr(gateway, "aclose", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result
        if document_gateway is not gateway:
            close = getattr(document_gateway, "aclose", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
        provider = app.state.llm_provider
        if provider and hasattr(provider, "aclose"):
            result = provider.aclose()
            if inspect.isawaitable(result):
                await result

    app = FastAPI(title="DataHub Document Enrichment API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(CatalogUnavailableError)
    async def catalog_unavailable(_: Request, __: CatalogUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "DataHub catalog unavailable"})

    @app.exception_handler(ConflictGatewayUnavailableError)
    async def conflict_unavailable(_: Request, __: ConflictGatewayUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": "DataHub document conflict check unavailable"}
        )

    @app.get("/api/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/ready")
    async def ready() -> dict[str, str]:
        await gateway.get_snapshot()
        return {"status": "ok", "database": "ok", "datahub": "ok"}

    @app.post("/api/analyses", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
    async def upload_analysis(
        file: Annotated[UploadFile, File(...)],
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
    ) -> UploadResponse:
        filename = Path(file.filename or "").name
        if Path(filename).suffix.casefold() not in {".md", ".txt"}:
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
        return UploadResponse(
            analysis=store.create(
                analysis_id=str(uuid4()),
                filename=filename,
                content=content,
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        )

    @app.get("/api/analyses", response_model=AnalysisListResponse)
    async def list_analyses(
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> AnalysisListResponse:
        items = store.list(limit=limit)
        return AnalysisListResponse(items=items, total=len(items))

    @app.delete("/api/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_analysis(
        analysis_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        publisher: Annotated[DocumentPublisher, Depends(get_publisher)],
    ) -> Response:
        try:
            record = store.get(analysis_id)
            if record.document_urn:
                await publisher.delete(record.document_urn)
            store.delete(analysis_id)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except PublishError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="DataHub document deletion failed; local record was kept",
            ) from exc

    @app.post("/api/analyses/{analysis_id}/recommend", response_model=AnalysisRecord)
    async def recommend_analysis(
        analysis_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
        provider: Annotated[LLMProvider | None, Depends(get_llm_provider)],
    ) -> AnalysisRecord:
        try:
            record = store.transition(analysis_id, AnalysisStatus.ANALYZING)
            content = store.content(analysis_id)
            snapshot = await gateway.get_snapshot()
            candidates, _ = await _retrieve_dataset_candidates(
                content, record.source_filename, snapshot, gateway
            )
            rule_recommendations = recommend_rules(content, record.source_filename, snapshot)
            if provider is None:
                recommendations = rule_recommendations
                recommendations.provider = "demo-rules"
            else:
                recommendations = await recommend_with_llm(
                    provider=provider,
                    text=content,
                    catalog=snapshot,
                    rule_recommendations=rule_recommendations,
                    dataset_candidates=candidates,
                )
            return store.save_recommendations(analysis_id, recommendations)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (CatalogUnavailableError, ProviderError) as exc:
            try:
                store.transition(
                    analysis_id, AnalysisStatus.ANALYSIS_FAILED, error_code=type(exc).__name__
                )
            except (AnalysisNotFoundError, InvalidStateError):
                pass
            LOGGER.warning(
                "recommendation failed for analysis %s: %s", analysis_id, type(exc).__name__
            )
            raise HTTPException(
                status_code=503, detail="Catalog or LLM recommendation unavailable"
            ) from exc

    @app.get("/api/analyses/{analysis_id}", response_model=AnalysisRecord)
    async def get_analysis(
        analysis_id: str, store: Annotated[SQLiteAnalysisStore, Depends(get_store)]
    ) -> AnalysisRecord:
        try:
            return store.get(analysis_id)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc

    @app.get("/api/analyses/{analysis_id}/source")
    async def download_source(
        analysis_id: str, store: Annotated[SQLiteAnalysisStore, Depends(get_store)]
    ) -> Response:
        try:
            record = store.get(analysis_id)
            return Response(
                content=store.content(analysis_id),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{record.source_filename}"'},
            )
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc

    @app.get(
        "/api/analyses/{analysis_id}/dataset-candidates", response_model=DatasetCandidatesResponse
    )
    async def get_dataset_candidates(
        analysis_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> DatasetCandidatesResponse:
        try:
            record = store.get(analysis_id)
            snapshot = await gateway.get_snapshot()
            items, degraded = await _retrieve_dataset_candidates(
                store.content(analysis_id), record.source_filename, snapshot, gateway
            )
            return DatasetCandidatesResponse(items=items, keyword_search_degraded=degraded)
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
            _validate_field_dispositions(selection, snapshot)
            actions = _review_actions(record.recommendations or RecommendationSet(), selection)
            updated = store.save_review(analysis_id, selection, actions)
            return ReviewResponse(analysis=updated, actions=store.review_actions(analysis_id))
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/analyses/{analysis_id}/review/field-check", response_model=FieldReviewResponse)
    async def review_field_check(
        analysis_id: str,
        selection: ReviewSelection,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> FieldReviewResponse:
        """Run one bounded field-disambiguation request for the complete draft."""
        try:
            store.get(analysis_id)
            snapshot = await gateway.get_snapshot()
            _validate_selection(selection, snapshot)
            return await _review_field_check(
                store.content(analysis_id),
                selection,
                snapshot,
                request_llm=app.state.llm_provider,
            )
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/analyses/{analysis_id}/return-to-review", response_model=AnalysisRecord)
    async def return_to_review(
        analysis_id: str, store: Annotated[SQLiteAnalysisStore, Depends(get_store)]
    ) -> AnalysisRecord:
        try:
            return store.return_to_review(analysis_id)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/api/analyses/{analysis_id}/review/draft", response_model=AnalysisRecord)
    async def save_review_draft(
        analysis_id: str, selection: ReviewSelection,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> AnalysisRecord:
        try:
            snapshot = await gateway.get_snapshot()
            _validate_selection(selection, snapshot)
            _validate_field_dispositions(selection, snapshot)
            return store.save_draft(analysis_id, selection)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/catalog/{entity_type}", response_model=CatalogSearchResponse)
    async def search_catalog(
        entity_type: CatalogEntityType,
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
        q: Annotated[str, Query(max_length=200)] = "",
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
    ) -> CatalogSearchResponse:
        items = await gateway.search(entity_type, q, limit)
        return CatalogSearchResponse(items=[_catalog_item(item) for item in items], limit=limit)

    @app.post("/api/catalog/refresh", response_model=CatalogRefreshResponse)
    async def refresh_catalog(
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> CatalogRefreshResponse:
        snapshot = await gateway.get_snapshot(force_refresh=True)
        return CatalogRefreshResponse(
            domains=len(snapshot.domains),
            tags=len(snapshot.tags),
            owners=len(snapshot.owners),
            datasets=len(snapshot.datasets),
        )

    @app.post("/api/analyses/{analysis_id}/publish", response_model=PublishResponse)
    async def publish_analysis(
        analysis_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
        conflict_gateway: Annotated[DocumentConflictGateway, Depends(get_conflict_gateway)],
        publisher: Annotated[DocumentPublisher, Depends(get_publisher)],
    ) -> PublishResponse:
        try:
            record = store.get(analysis_id)
            if record.status == AnalysisStatus.PUBLISHED:
                return _publish_response(record, app_settings)
            if record.status not in {AnalysisStatus.APPROVED, AnalysisStatus.PUBLISH_FAILED}:
                raise InvalidStateError(f"Cannot publish analysis in {record.status.value}")
            if record.final_selection is None:
                raise InvalidStateError("A reviewed selection is required before publishing")
            snapshot = await gateway.get_snapshot()
            _validate_selection(record.final_selection, snapshot)
            content = store.content(record.id)
            validation = _schema_validation(record, content, snapshot, store)
            if any(item.high_risk and not item.confirmed for item in validation.references):
                raise InvalidStateError(
                    "High-risk unresolved schema fields require explicit confirmation before publishing"
                )
            title = title_from_content(content, record.source_filename)
            candidates = detect_conflicts(
                documents=await _retrieve_conflict_documents(
                    conflict_gateway, record.final_selection, title
                ),
                selection=record.final_selection,
                current_document_urn=f"urn:li:document:doc-enrichment-{record.id}",
                title=title,
                content=content,
                detected_at=utcnow(),
            )
            store.replace_conflicts(record.id, candidates)
            if any(
                candidate.high_risk and not candidate.confirmed
                for candidate in store.conflicts(record.id)
            ):
                raise InvalidStateError(
                    "High-risk document conflicts require explicit confirmation before publishing"
                )
            record = store.transition(analysis_id, AnalysisStatus.PUBLISHING)
            published = await publisher.publish(
                analysis_id=record.id,
                filename=record.source_filename,
                source_sha256=record.source_sha256,
                content=content,
                selection=record.final_selection,
            )
            updated = store.save_publish_result(
                record.id,
                document_urn=published.urn,
                dataset_baseline_json=json.dumps(
                    _dataset_baseline(record.final_selection, snapshot, store.content(record.id)),
                    sort_keys=True,
                ),
                published_at=utcnow(),
            )
            return _publish_response(updated, app_settings)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ConflictGatewayUnavailableError:
            # The pre-write safety check failed, so deliberately do not transition or write.
            raise
        except (PublishVerificationError, Exception) as exc:
            # Preserve the UNPUBLISHED DataHub entity for a safe retry; never delete it automatically.
            try:
                store.save_publish_failure(analysis_id, type(exc).__name__)
            except (AnalysisNotFoundError, InvalidStateError):
                pass
            LOGGER.warning("publish failed for analysis %s: %s", analysis_id, type(exc).__name__)
            raise HTTPException(
                status_code=502, detail="DataHub document publish failed; retry is safe"
            ) from exc

    @app.post("/api/analyses/{analysis_id}/freshness", response_model=FreshnessCheckResponse)
    async def check_freshness(
        analysis_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> FreshnessCheckResponse:
        try:
            record = store.get(analysis_id)
            if not record.document_urn:
                raise InvalidStateError("Freshness can only be checked after publishing")
            baseline = _read_dataset_baseline(store, analysis_id)
            snapshot = await gateway.get_snapshot()
            evidence = _freshness_evidence(baseline, snapshot)
            differences = [item.message for item in evidence]
            updated = store.save_freshness_check(
                analysis_id,
                reason="; ".join(differences) if differences else None,
                evidence=evidence,
                checked_at=utcnow(),
            )
            return FreshnessCheckResponse(
                analysis=updated,
                changed=bool(differences),
                differences=differences,
                evidence=evidence,
            )
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/analyses/{analysis_id}/freshness/acknowledge", response_model=AnalysisRecord)
    async def acknowledge_freshness(
        analysis_id: str, store: Annotated[SQLiteAnalysisStore, Depends(get_store)]
    ) -> AnalysisRecord:
        try:
            return store.acknowledge_freshness(analysis_id)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/freshness/recent-changes", response_model=RecentChangeCheckResponse)
    async def check_recent_database_changes(
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
    ) -> RecentChangeCheckResponse:
        changes = recent_database_changes()
        records = store.published_for_datasets({change.dataset_urn for change in changes})
        affected: list[str] = []
        for record in records:
            evidence = _mock_change_evidence(store.content(record.id), changes, record)
            store.save_freshness_check(
                record.id,
                reason="; ".join(item.message for item in evidence) if evidence else None,
                evidence=evidence,
                checked_at=utcnow(),
            )
            if evidence:
                affected.append(record.id)
        return RecentChangeCheckResponse(
            changes=changes,
            checked_analysis_ids=[record.id for record in records],
            affected_analysis_ids=affected,
        )

    @app.post(
        "/api/analyses/{analysis_id}/freshness/apply-suggestion", response_model=AnalysisRecord
    )
    async def apply_freshness_suggestion(
        analysis_id: str,
        evidence_index: Annotated[int, Query(ge=0)],
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        publisher: Annotated[DocumentPublisher, Depends(get_publisher)],
    ) -> AnalysisRecord:
        try:
            record = store.get(analysis_id)
            if record.status != AnalysisStatus.PUBLISHED or record.final_selection is None:
                raise InvalidStateError("Suggestions can only be applied to published documents")
            evidence = record.freshness_evidence[evidence_index]
            if not evidence.proposed_content or (evidence.recommendation_confidence or 0) < 0.9:
                raise InvalidStateError("This suggestion requires manual review")
            content = evidence.proposed_content
            await publisher.publish(
                analysis_id=record.id,
                filename=record.source_filename,
                source_sha256=hashlib.sha256(content.encode()).hexdigest(),
                content=content,
                selection=record.final_selection,
            )
            return store.apply_suggested_content(
                record.id, content, hashlib.sha256(content.encode()).hexdigest()
            )
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="Suggestion not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/analyses/{analysis_id}/conflicts", response_model=ConflictReviewResponse)
    async def get_conflicts(
        analysis_id: str, store: Annotated[SQLiteAnalysisStore, Depends(get_store)]
    ) -> ConflictReviewResponse:
        try:
            store.get(analysis_id)
            return ConflictReviewResponse(candidates=store.conflicts(analysis_id))
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc

    @app.post("/api/analyses/{analysis_id}/conflicts/check", response_model=ConflictReviewResponse)
    async def check_conflicts(
        analysis_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        conflict_gateway: Annotated[DocumentConflictGateway, Depends(get_conflict_gateway)],
    ) -> ConflictReviewResponse:
        try:
            record = store.get(analysis_id)
            if record.final_selection is None:
                raise InvalidStateError(
                    "A reviewed selection is required before checking conflicts"
                )
            content = store.content(analysis_id)
            title = title_from_content(content, record.source_filename)
            candidates = detect_conflicts(
                documents=await _retrieve_conflict_documents(
                    conflict_gateway, record.final_selection, title
                ),
                selection=record.final_selection,
                current_document_urn=f"urn:li:document:doc-enrichment-{record.id}",
                title=title,
                content=content,
                detected_at=utcnow(),
            )
            store.replace_conflicts(analysis_id, candidates)
            return ConflictReviewResponse(candidates=store.conflicts(analysis_id))
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/analyses/{analysis_id}/review/conflicts", response_model=ConflictReviewResponse)
    async def review_conflicts(
        analysis_id: str,
        selection: ReviewSelection,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
        conflict_gateway: Annotated[DocumentConflictGateway, Depends(get_conflict_gateway)],
    ) -> ConflictReviewResponse:
        try:
            record = store.get(analysis_id)
            snapshot = await gateway.get_snapshot()
            _validate_selection(selection, snapshot)
            content = store.content(analysis_id)
            title = title_from_content(content, record.source_filename)
            candidates = detect_conflicts(
                documents=await _retrieve_conflict_documents(conflict_gateway, selection, title),
                selection=selection,
                current_document_urn=f"urn:li:document:doc-enrichment-{record.id}",
                title=title,
                content=content,
                detected_at=utcnow(),
            )
            candidates = await _semantic_conflicts(
                content, record.source_filename, candidates, app.state.llm_provider
            )
            return ConflictReviewResponse(candidates=candidates)
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put(
        "/api/analyses/{analysis_id}/conflicts/{document_urn:path}/confirm",
        response_model=ConflictReviewResponse,
    )
    async def confirm_conflict(
        analysis_id: str,
        document_urn: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
    ) -> ConflictReviewResponse:
        try:
            store.get(analysis_id)
            return ConflictReviewResponse(
                candidates=store.confirm_conflict(analysis_id, document_urn)
            )
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conflict candidate not found") from exc

    @app.post(
        "/api/analyses/{analysis_id}/schema-validation", response_model=SchemaValidationResponse
    )
    async def schema_validation(
        analysis_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> SchemaValidationResponse:
        try:
            record = store.get(analysis_id)
            if record.final_selection is None:
                raise InvalidStateError("A reviewed selection is required before schema validation")
            return _schema_validation(
                record, store.content(analysis_id), await gateway.get_snapshot(), store
            )
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put(
        "/api/analyses/{analysis_id}/schema-validation/{reference_id}/confirm",
        response_model=SchemaValidationResponse,
    )
    async def confirm_schema_validation(
        analysis_id: str,
        reference_id: str,
        store: Annotated[SQLiteAnalysisStore, Depends(get_store)],
        gateway: Annotated[DataHubCatalogGateway, Depends(get_gateway)],
    ) -> SchemaValidationResponse:
        try:
            record = store.get(analysis_id)
            if record.final_selection is None:
                raise InvalidStateError("A reviewed selection is required before schema validation")
            validation = _schema_validation(
                record, store.content(analysis_id), await gateway.get_snapshot(), store
            )
            if reference_id not in {item.id for item in validation.references}:
                raise KeyError(reference_id)
            store.confirm_schema_reference(analysis_id, reference_id)
            return _schema_validation(
                record, store.content(analysis_id), await gateway.get_snapshot(), store
            )
        except AnalysisNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Analysis not found") from exc
        except InvalidStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Schema reference not found") from exc

    return app


def _publish_response(record: AnalysisRecord, settings: Settings) -> PublishResponse:
    assert record.document_urn
    base = str(settings.datahub_ui_url).rstrip("/")
    return PublishResponse(
        analysis=record,
        document_urn=record.document_urn,
        datahub_document_url=f"{base}/document/{record.document_urn}",
        related_dataset_urls=[
            f"{base}/dataset/{urn}"
            for urn in (record.final_selection.dataset_urns if record.final_selection else [])
        ],
    )


def _schema_validation(
    record: AnalysisRecord, content: str, snapshot, store: SQLiteAnalysisStore
) -> SchemaValidationResponse:
    assert record.final_selection is not None
    selected = [
        item for item in snapshot.datasets if item.urn in set(record.final_selection.dataset_urns)
    ]
    confirmed = store.confirmed_schema_references(record.id)
    confirmed.update(item.reference_id for item in record.final_selection.field_dispositions)
    references = [
        item.model_copy(update={"confirmed": item.id in confirmed})
        for item in lint_schema_references(content, selected)
    ]
    return SchemaValidationResponse(checked_at=utcnow(), references=references)


def _selected_datasets(selection: ReviewSelection, snapshot) -> list[Dataset]:
    return [item for item in snapshot.datasets if item.urn in set(selection.dataset_urns)]


def _validate_field_dispositions(selection: ReviewSelection, snapshot) -> None:
    allowed_actions = {"accept_suggestion", "map_dataset", "business_term", "keep_unresolved"}
    selected = {item.urn for item in _selected_datasets(selection, snapshot)}
    seen: set[str] = set()
    for item in selection.field_dispositions:
        if item.reference_id in seen:
            raise ValueError("duplicate field disposition")
        seen.add(item.reference_id)
        if item.action not in allowed_actions:
            raise ValueError("invalid field disposition action")
        if item.action in {"accept_suggestion", "map_dataset"} and item.dataset_urn not in selected:
            raise ValueError("field mapping must use a selected Dataset")


async def _review_field_check(
    content: str,
    selection: ReviewSelection,
    snapshot,
    request_llm: LLMProvider | None,
) -> FieldReviewResponse:
    selected = _selected_datasets(selection, snapshot)
    references = lint_schema_references(content, selected)
    dispositions = {item.reference_id for item in selection.field_dispositions}
    references = [
        item.model_copy(update={"confirmed": item.id in dispositions}) for item in references
    ]
    pending = [item for item in references if item.status in {"ambiguous", "unresolved"}]
    if not pending:
        return FieldReviewResponse(
            checked_at=utcnow(), references=references, suggestions=[], provider_status="available"
        )
    if request_llm is None:
        return FieldReviewResponse(
            checked_at=utcnow(),
            references=references,
            suggestions=[],
            provider_status="unavailable",
        )
    datasets = [
        {
            "urn": item.urn,
            "name": item.qualified_name,
            "description": item.description[:800],
            "schema": item.schema_fields[:100],
        }
        for item in selected
    ]
    fields = [
        {
            "reference_id": item.id,
            "field": item.field_path,
            "location": item.location,
            "candidate_dataset_urns": item.candidate_dataset_urns,
        }
        for item in pending
    ]
    try:
        result = await request_llm.disambiguate_fields(
            document=_trim_review_document(content), datasets=datasets, fields=fields
        )
        by_id = {item.id: item for item in pending}
        if len(result.fields) != len(pending) or {
            item.reference_id for item in result.fields
        } != set(by_id):
            raise ProviderError("LLM field response omitted a requested field")
        suggestions: list[FieldSuggestion] = []
        for item in result.fields:
            reference = by_id[item.reference_id]
            if item.dataset_urn not in reference.candidate_dataset_urns:
                raise ProviderError("LLM returned a Dataset outside the field whitelist")
            suggestions.append(
                FieldSuggestion(
                    reference_id=item.reference_id,
                    dataset_urn=item.dataset_urn,
                    confidence=item.confidence,
                    reason=item.reason,
                )
            )
        return FieldReviewResponse(
            checked_at=utcnow(),
            references=references,
            suggestions=suggestions,
            provider_status="available",
        )
    except ProviderError:
        return FieldReviewResponse(
            checked_at=utcnow(),
            references=references,
            suggestions=[],
            provider_status="invalid_response",
        )


def _trim_review_document(content: str) -> str:
    # The original document is untrusted.  A bounded context still preserves title and late SQL/definitions.
    return (
        content
        if len(content) <= 12_000
        else f"{content[:10_000]}\n\n[truncated]\n\n{content[-1_500:]}"
    )


async def _semantic_conflicts(content: str, filename: str, candidates, provider: LLMProvider | None):
    """Classify the complete recall set in one request for the Review UI."""
    if not candidates or provider is None:
        return candidates
    try:
        response = await provider.classify_conflicts(
            document={"title": title_from_content(content, filename), "content": _trim_review_document(content)},
            candidates=[{"document_urn": item.document_urn, "title": item.title, "content": "",
                         "related_dataset_urns": item.related_dataset_urns, "rule_evidence": item.evidence}
                        for item in candidates],
        )
        results = [item.model_dump() for item in response.documents]
        expected = {item.document_urn for item in candidates}
        if len(results) != len(candidates) or {item["document_urn"] for item in results} != expected:
            raise ProviderError("LLM conflict response omitted a candidate")
        if any(item["classification"] not in {"duplicate", "conflicting", "related", "unrelated"} for item in results):
            raise ProviderError("LLM returned an invalid conflict class")
        classified = {item["document_urn"]: item for item in results}
        return [
            candidate.model_copy(
                update={
                    "semantic_classification": classified[candidate.document_urn]["classification"],
                    "semantic_confidence": classified[candidate.document_urn]["confidence"],
                    "semantic_reason": classified[candidate.document_urn]["reason"],
                    "high_risk": classified[candidate.document_urn]["classification"]
                    in {"duplicate", "conflicting"}
                    and classified[candidate.document_urn]["confidence"] >= 0.85,
                }
            )
            for candidate in candidates
        ]
    except (ProviderError, KeyError, TypeError):
        # A missing or invalid model result is visible as pending but can never upgrade
        # a lexical candidate into a publish gate.
        return candidates


async def _retrieve_dataset_candidates(
    content: str, filename: str, snapshot, gateway: DataHubCatalogGateway
) -> tuple[list, bool]:
    deterministic = dataset_candidates(content, filename, snapshot, limit=30)
    extracted = extract_document(content, filename)
    # Query only concise, deterministic document terms. SQL references are the most
    # useful query because DataHub's native search can supplement an incomplete cache.
    terms = [reference.text for reference in extracted.table_references]
    if not terms:
        terms = [extracted.title]
    keyword_results: list[Dataset] = []
    try:
        for term in dict.fromkeys(terms):
            results = await gateway.keyword_search_datasets(term, limit=30)
            # A SQL table reference is already strong Dataset-level evidence.  Keep
            # the native search only as a recall fallback for that same table,
            # rather than exposing every loosely related Dataset as a suggestion.
            if extracted.table_references:
                normalized = term.casefold()
                keyword_results.extend(
                    item
                    for item in results
                    if normalized
                    in {
                        item.name.casefold(),
                        item.qualified_name.casefold(),
                        item.qualified_name.casefold().rsplit(".", 1)[-1],
                    }
                )
            else:
                keyword_results.extend(results)
    except CatalogUnavailableError:
        # Candidate recall remains deterministic when DataHub keyword search is unavailable.
        return deterministic, True
    return merge_dataset_candidates(deterministic, keyword_results, limit=30), False


def _dataset_baseline(
    selection: ReviewSelection, snapshot, content: str
) -> list[dict[str, object]]:
    wanted = set(selection.dataset_urns)
    selected = [item for item in snapshot.datasets if item.urn in wanted]
    references_by_dataset: dict[str, set[str]] = {}
    for reference in lint_schema_references(content, selected):
        for urn in reference.candidate_dataset_urns:
            if reference.status == "resolved":
                references_by_dataset.setdefault(urn, set()).add(reference.field_path)
    return [
        {
            "urn": item.urn,
            "description": item.description,
            "schema_fields": sorted(item.schema_fields),
            "schema_fingerprint": _schema_fingerprint(item),
            "field_snapshots": [
                field.model_dump()
                for field in sorted(item.field_snapshots, key=lambda field: field.field_path)
            ],
            "domain_urn": item.domain_urn,
            "owner_urns": sorted(item.owner_urns),
            "tag_urns": sorted(item.tag_urns),
            "deprecated": item.deprecated,
            "referenced_fields": sorted(references_by_dataset.get(item.urn, set())),
        }
        for item in snapshot.datasets
        if item.urn in wanted
    ]


def _read_dataset_baseline(store: SQLiteAnalysisStore, analysis_id: str) -> list[dict[str, object]]:
    with (
        store._connect() as connection
    ):  # Store owns the SQLite boundary; no DataHub mutation occurs here.
        row = connection.execute(
            "SELECT dataset_baseline_json FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
    return json.loads(row["dataset_baseline_json"] or "[]")


def _freshness_differences(baseline: list[dict[str, object]], snapshot) -> list[str]:
    """Legacy-readable projection retained for callers and existing integrations."""
    return [item.message for item in _freshness_evidence(baseline, snapshot)]


def _freshness_evidence(baseline: list[dict[str, object]], snapshot) -> list[FreshnessDifference]:
    current = {item.urn: item for item in snapshot.datasets}
    differences: list[FreshnessDifference] = []

    def add(
        urn: str,
        category: str,
        message: str,
        *,
        field_path: str | None = None,
        old=None,
        new=None,
        referenced=False,
    ) -> None:
        differences.append(
            FreshnessDifference(
                dataset_urn=urn,
                category=category,
                field_path=field_path,
                old_value=old,
                new_value=new,
                affects_referenced_field=referenced,
                message=message,
            )
        )

    for previous in baseline:
        urn = str(previous["urn"])
        item = current.get(urn)
        if item is None:
            add(urn, "dataset_removed", f"Related dataset no longer exists: {urn}")
            continue
        if item.deprecated and not previous.get("deprecated", False):
            add(
                urn, "deprecation", f"Related dataset is now deprecated: {urn}", old=False, new=True
            )
        fields = sorted(item.schema_fields)
        previous_fields = {
            field["field_path"]: field for field in previous.get("field_snapshots", [])
        }
        current_fields = {field.field_path: field.model_dump() for field in item.field_snapshots}
        for field_path in sorted(set(current_fields) - set(previous_fields)):
            add(
                urn,
                "field_added",
                f"{urn} added schema field {field_path}: new={current_fields[field_path]}",
                field_path=field_path,
                new=current_fields[field_path],
            )
        for field_path in sorted(set(previous_fields) - set(current_fields)):
            add(
                urn,
                "field_removed",
                f"{urn} removed schema field {field_path}: old={previous_fields[field_path]}",
                field_path=field_path,
                old=previous_fields[field_path],
                referenced=field_path in previous.get("referenced_fields", []),
            )
        for field_path in sorted(set(previous_fields) & set(current_fields)):
            for key in ("native_data_type", "nullable", "description"):
                if previous_fields[field_path].get(key) != current_fields[field_path].get(key):
                    add(
                        urn,
                        f"field_{key}",
                        f"{urn} changed schema field {field_path} {key}: old={previous_fields[field_path].get(key)!r} new={current_fields[field_path].get(key)!r}",
                        field_path=field_path,
                        old=previous_fields[field_path].get(key),
                        new=current_fields[field_path].get(key),
                        referenced=field_path in previous.get("referenced_fields", []),
                    )
        removed = sorted(set(previous.get("referenced_fields", [])) - set(fields))
        if removed:
            add(
                urn,
                "referenced_field_removed",
                f"{urn} removed referenced schema fields: {', '.join(removed)}",
                old=removed,
                referenced=True,
            )
        for key, actual in (
            ("description", item.description),
            ("domain_urn", item.domain_urn),
            ("owner_urns", sorted(item.owner_urns)),
            ("tag_urns", sorted(item.tag_urns)),
        ):
            if previous[key] != actual:
                add(
                    urn,
                    key,
                    f"{urn} changed {key}: old={previous[key]!r} new={actual!r}",
                    old=previous[key],
                    new=actual,
                )
    return differences


def _mock_change_evidence(
    content: str, changes, record: AnalysisRecord
) -> list[FreshnessDifference]:
    selected = set(record.final_selection.dataset_urns if record.final_selection else [])
    evidence: list[FreshnessDifference] = []
    for change in changes:
        if change.dataset_urn not in selected or change.field_path not in content:
            continue
        proposed = content.replace(
            change.field_path, change.replacement_field_path or change.field_path
        )
        evidence.append(
            FreshnessDifference(
                dataset_urn=change.dataset_urn,
                category=change.kind,
                field_path=change.field_path,
                old_value=change.field_path,
                new_value=change.replacement_field_path,
                affects_referenced_field=True,
                message=f"Migration {change.migration_id}: {change.summary}",
                recommendation_confidence=0.98,
                proposed_content=proposed,
            )
        )
    return evidence


def _schema_fingerprint(dataset: Dataset) -> str:
    canonical = [
        field.model_dump()
        for field in sorted(dataset.field_snapshots, key=lambda field: field.field_path)
    ]
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _conflict_query(selection: ReviewSelection, title: str) -> str:
    # searchDocuments is a full-text endpoint, not an asset-URN filter.  Putting
    # URNs in the query makes otherwise relevant Documents disappear from recall.
    # Dataset/domain matching happens deterministically after bounded retrieval.
    del selection
    return title[:500]


async def _retrieve_conflict_documents(
    gateway: DocumentConflictGateway, selection: ReviewSelection, title: str
) -> list:
    """Broaden DataHub's AND-like full-text retrieval without broad catalog scans."""
    queries = [_conflict_query(selection, title)]
    queries.extend(token for token in re.findall(r"[\w-]+", title.casefold()) if len(token) >= 3)
    documents: dict[str, object] = {}
    for query in dict.fromkeys(queries):
        for document in await gateway.search_documents(query=query, limit=20):
            documents.setdefault(document.urn, document)
            if len(documents) >= 20:
                return list(documents.values())
    return list(documents.values())


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
        qualified_name=item.qualified_name if isinstance(item, Dataset) else None,
        owner_type=item.owner_type if isinstance(item, Owner) else None,
        title=item.title if isinstance(item, Owner) else None,
    )


def _review_actions(
    recommendations: RecommendationSet, selection: ReviewSelection
) -> list[ReviewAction]:
    now = datetime.now(UTC)
    pairs = [
        (
            EntityType.DOMAIN,
            [recommendations.domain.urn] if recommendations.domain else [],
            [selection.domain_urn] if selection.domain_urn else [],
        ),
        (EntityType.TAG, [item.urn for item in recommendations.tags], selection.tag_urns),
        (
            EntityType.OWNER,
            [recommendations.owner.urn] if recommendations.owner else [],
            [selection.owner_urn] if selection.owner_urn else [],
        ),
        (
            EntityType.DATASET,
            [item.urn for item in recommendations.datasets],
            selection.dataset_urns,
        ),
    ]
    actions: list[ReviewAction] = []
    for entity_type, recommended, selected in pairs:
        recommended_set, selected_set = set(recommended), set(selected)
        for urn in sorted(recommended_set & selected_set):
            actions.append(
                ReviewAction(entity_type=entity_type, urn=urn, action="accepted", created_at=now)
            )
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
            actions.append(
                ReviewAction(entity_type=entity_type, urn=urn, action="removed", created_at=now)
            )
    return actions


app = create_app()
