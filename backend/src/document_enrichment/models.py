from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class EntityType(StrEnum):
    DOMAIN = "domain"
    TAG = "tag"
    OWNER = "owner"
    DATASET = "dataset"


class AnalysisStatus(StrEnum):
    UPLOADED = "UPLOADED"
    ANALYZING = "ANALYZING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    PUBLISHED = "PUBLISHED"


class DocumentFreshnessStatus(StrEnum):
    ACTIVE = "ACTIVE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class Evidence(BaseModel):
    kind: str
    matched_text: str
    location: str


class CatalogEntity(BaseModel):
    urn: str
    name: str
    description: str = ""


class Domain(CatalogEntity):
    pass


class Tag(CatalogEntity):
    pass


class Owner(CatalogEntity):
    owner_type: str  # CORP_USER or CORP_GROUP
    title: str = ""


class Dataset(CatalogEntity):
    qualified_name: str
    schema_fields: list[str] = Field(default_factory=list, max_length=100)
    owner_urns: list[str] = Field(default_factory=list)
    domain_urn: str | None = None
    tag_urns: list[str] = Field(default_factory=list)
    deprecated: bool = False
    field_snapshots: list[SchemaField] = Field(default_factory=list, max_length=100)


class SchemaField(BaseModel):
    """The structured schema projection used for publishing baselines."""

    field_path: str
    native_data_type: str | None = None
    nullable: bool | None = None
    description: str = ""


class CatalogSnapshot(BaseModel):
    domains: list[Domain] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    owners: list[Owner] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)


class Recommendation(BaseModel):
    urn: str
    display_name: str
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    evidence: list[Evidence] = Field(default_factory=list)
    source: str

    @field_validator("urn")
    @classmethod
    def must_be_datahub_urn(cls, value: str) -> str:
        if not value.startswith("urn:li:"):
            raise ValueError("recommendation urn must be a DataHub URN")
        return value


class RecommendationSet(BaseModel):
    domain: Recommendation | None = None
    # Five is a presentation default, not the candidate-pool size.
    tags: list[Recommendation] = Field(default_factory=list, max_length=5)
    owner: Recommendation | None = None
    datasets: list[Recommendation] = Field(default_factory=list, max_length=5)
    provider: str = "rule"
    model: str | None = None
    elapsed_ms: int = Field(default=0, ge=0)


class ReviewSelection(BaseModel):
    domain_urn: str | None = None
    tag_urns: list[str] = Field(default_factory=list, max_length=20)
    owner_urn: str | None = None
    dataset_urns: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tag_urns", "dataset_urns")
    @classmethod
    def no_duplicates(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate URNs are not allowed")
        return value


class ReviewAction(BaseModel):
    entity_type: EntityType
    urn: str
    action: str  # accepted, removed, replaced
    replaced_urn: str | None = None
    created_at: datetime


class AnalysisRecord(BaseModel):
    id: str
    source_filename: str
    source_sha256: str
    character_count: int
    status: AnalysisStatus
    recommendations: RecommendationSet | None = None
    final_selection: ReviewSelection | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime
    review_started_at: datetime | None = None
    review_completed_at: datetime | None = None
    document_urn: str | None = None
    published_at: datetime | None = None
    freshness_status: DocumentFreshnessStatus | None = None
    freshness_reason: str | None = None
    last_freshness_checked_at: datetime | None = None


class UploadResponse(BaseModel):
    analysis: AnalysisRecord


class CatalogSearchItem(CatalogEntity):
    qualified_name: str | None = None
    owner_type: str | None = None
    title: str | None = None


class CatalogSearchResponse(BaseModel):
    items: list[CatalogSearchItem]
    limit: int


class DatasetCandidatesResponse(BaseModel):
    """Evidence-first retrieval pool used to constrain the LLM and review UI."""

    items: list[Recommendation] = Field(default_factory=list, max_length=30)
    keyword_search_degraded: bool = False


class CatalogRefreshResponse(BaseModel):
    domains: int
    tags: int
    owners: int
    datasets: int


class PublishResponse(BaseModel):
    analysis: AnalysisRecord
    document_urn: str
    datahub_document_url: str
    related_dataset_urls: list[str]


class FreshnessCheckResponse(BaseModel):
    analysis: AnalysisRecord
    changed: bool
    differences: list[str] = Field(default_factory=list)


class DocumentConflictCandidate(BaseModel):
    document_urn: str
    title: str
    related_dataset_urns: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    detector_version: str
    detected_at: datetime
    high_risk: bool = False
    confirmed: bool = False


class ConflictReviewResponse(BaseModel):
    candidates: list[DocumentConflictCandidate] = Field(default_factory=list)
