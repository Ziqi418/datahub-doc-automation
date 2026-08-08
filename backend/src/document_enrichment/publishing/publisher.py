"""The only component allowed to write native DataHub Documents."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from document_enrichment.config import Settings
from document_enrichment.models import ReviewSelection


class PublishError(RuntimeError):
    pass


class PublishVerificationError(PublishError):
    pass


@dataclass(frozen=True)
class PublishedDocument:
    urn: str


class DocumentPublisher(Protocol):
    async def publish(
        self, *, analysis_id: str, filename: str, source_sha256: str, content: str,
        selection: ReviewSelection,
    ) -> PublishedDocument: ...


class DataHubDocumentPublisher:
    """SDK v2 publisher which keeps a partially written document unsearchable."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def publish(
        self, *, analysis_id: str, filename: str, source_sha256: str, content: str,
        selection: ReviewSelection,
    ) -> PublishedDocument:
        return await asyncio.to_thread(
            self._publish_sync, analysis_id, filename, source_sha256, content, selection
        )

    def _publish_sync(
        self, analysis_id: str, filename: str, source_sha256: str, content: str,
        selection: ReviewSelection,
    ) -> PublishedDocument:
        try:
            from datahub.metadata.schema_classes import DocumentStateClass
            from datahub.sdk import DataHubClient, Document
        except ImportError as exc:  # pragma: no cover - installation issue
            raise PublishError("DataHub SDK is not installed; install the datahub extra") from exc

        document_id = f"doc-enrichment-{analysis_id}"
        expected_urn = f"urn:li:document:{document_id}"
        title = _title_from_markdown(content, filename)
        properties = {
            "source_filename": filename,
            "source_sha256": source_sha256,
            "enrichment_analysis_id": analysis_id,
            "enrichment_app_version": "0.1.0",
            "published_at": datetime.now(UTC).isoformat(),
        }
        document = Document.create_document(
            id=document_id, title=title, text=content, status=DocumentStateClass.UNPUBLISHED,
            related_assets=selection.dataset_urns, owners=[selection.owner_urn] if selection.owner_urn else None,
            tags=selection.tag_urns, domain=selection.domain_urn, custom_properties=properties,
        )
        client = DataHubClient(server=str(self.settings.datahub_gms_url), token=self.settings.datahub_token)
        try:
            client.entities.upsert(document)
            saved = client.entities.get(expected_urn)
            _verify(saved, title, content, selection)
            saved.publish()
            client.entities.update(saved)
            published = client.entities.get(expected_urn)
            _verify(published, title, content, selection, expected_status=DocumentStateClass.PUBLISHED)
        except Exception as exc:
            if isinstance(exc, PublishError):
                raise
            raise PublishError(f"DataHub document publish failed: {type(exc).__name__}") from exc
        return PublishedDocument(urn=expected_urn)


def _title_from_markdown(content: str, filename: str) -> str:
    for line in content.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()[:255]
    return filename.rsplit(".", 1)[0][:255] or "Untitled document"


def _verify(document: object, title: str, content: str, selection: ReviewSelection,
            expected_status: str = "UNPUBLISHED") -> None:
    def values(name: str) -> set[str]:
        value = getattr(document, name, []) or []
        # SDK relation objects expose their target as owner/tag; related assets are strings.
        return {str(getattr(item, "owner", getattr(item, "tag", item))) for item in value}

    if getattr(document, "title", None) != title or getattr(document, "text", None) != content:
        raise PublishVerificationError("DataHub read-back did not preserve document title or body")
    if getattr(document, "status", None) != expected_status:
        raise PublishVerificationError("DataHub read-back returned an unexpected document status")
    if str(getattr(document, "domain", None) or "") != (selection.domain_urn or ""):
        raise PublishVerificationError("DataHub read-back did not preserve document domain")
    if values("tags") != set(selection.tag_urns):
        raise PublishVerificationError("DataHub read-back did not preserve document tags")
    if values("owners") != ({selection.owner_urn} if selection.owner_urn else set()):
        raise PublishVerificationError("DataHub read-back did not preserve document owner")
    if values("related_assets") != set(selection.dataset_urns):
        raise PublishVerificationError("DataHub read-back did not preserve related datasets")
