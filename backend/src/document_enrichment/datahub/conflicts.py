from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from document_enrichment.config import Settings
from document_enrichment.models import DocumentConflictCandidate, ReviewSelection


class ConflictGatewayUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExistingDocument:
    urn: str
    title: str
    text: str
    related_dataset_urns: list[str]
    domain_urn: str | None


class DocumentConflictGateway(Protocol):
    async def search_documents(self, *, query: str, limit: int = 20) -> list[ExistingDocument]: ...


_SEARCH_DOCUMENTS_QUERY = """
query SearchDocuments($input: SearchDocumentsInput!) {
  searchDocuments(input: $input) {
    documents {
      urn
      info {
        title
        contents { text }
        relatedAssets { asset { urn } }
      }
      domain { domain { urn } }
    }
  }
}
"""


class GraphQLDocumentConflictGateway:
    """Read-only adapter for DataHub's v1.6 `searchDocuments` query."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.http_timeout_seconds)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search_documents(self, *, query: str, limit: int = 20) -> list[ExistingDocument]:
        headers = {"Content-Type": "application/json"}
        if self._settings.datahub_token:
            headers["Authorization"] = f"Bearer {self._settings.datahub_token}"
        try:
            response = await self._client.post(
                str(self._settings.datahub_graphql_url),
                json={"query": _SEARCH_DOCUMENTS_QUERY, "variables": {"input": {"query": query, "start": 0, "count": limit}}},
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConflictGatewayUnavailableError("DataHub document search failed") from exc
        if body.get("errors"):
            raise ConflictGatewayUnavailableError("DataHub document search returned errors")
        result = body.get("data", {}).get("searchDocuments", {})
        rows = result.get("documents", []) if isinstance(result, dict) else []
        documents: list[ExistingDocument] = []
        for document in rows:
            if not isinstance(document, dict) or not document.get("urn"):
                continue
            info = document.get("info") if isinstance(document.get("info"), dict) else {}
            contents = info.get("contents") if isinstance(info.get("contents"), dict) else {}
            domain = document.get("domain") if isinstance(document.get("domain"), dict) else {}
            related_assets = info.get("relatedAssets", [])
            documents.append(ExistingDocument(
                urn=document["urn"], title=info.get("title") or "", text=contents.get("text") or "",
                related_dataset_urns=sorted(
                    asset["asset"]["urn"]
                    for asset in related_assets
                    if isinstance(asset, dict)
                    and isinstance(asset.get("asset"), dict)
                    and str(asset["asset"].get("urn", "")).startswith("urn:li:dataset:")
                ),
                domain_urn=(domain.get("domain") or {}).get("urn") if isinstance(domain.get("domain"), dict) else None,
            ))
        return documents


_WORD = re.compile(r"[\w-]+", re.UNICODE)
_HIGH_RISK = {"metric", "metrics", "formula", "definition", "kpi", "口径", "指标", "公式", "定义"}
DETECTOR_VERSION = "deterministic-v1"


def title_from_content(content: str, filename: str) -> str:
    for line in content.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()[:255]
    return filename.rsplit(".", 1)[0][:255]


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in _WORD.findall(value) if len(item) > 1}


def detect_conflicts(*, documents: list[ExistingDocument], selection: ReviewSelection,
                     current_document_urn: str, title: str, content: str, detected_at) -> list[DocumentConflictCandidate]:
    wanted_datasets = set(selection.dataset_urns)
    title_tokens, body_tokens = _tokens(title), _tokens(content)
    sql_tables = set(re.findall(r"(?i)\b(?:from|join|update|into)\s+([\w.]+)", content))
    high_risk_source = bool(_tokens(content) & _HIGH_RISK)
    candidates: list[DocumentConflictCandidate] = []
    for document in documents:
        if document.urn == current_document_urn:
            continue
        evidence: list[str] = []
        shared_datasets = sorted(wanted_datasets & set(document.related_dataset_urns))
        if shared_datasets:
            evidence.append("shared datasets: " + ", ".join(shared_datasets))
        other_title = _tokens(document.title)
        union = title_tokens | other_title
        similarity = len(title_tokens & other_title) / len(union) if union else 0.0
        if similarity >= 0.25:
            evidence.append(f"title token similarity: {similarity:.2f}")
        keyword_overlap = sorted(body_tokens & _tokens(document.text))
        if len(keyword_overlap) >= 2:
            evidence.append("body keywords: " + ", ".join(keyword_overlap[:8]))
        table_overlap = sorted(sql_tables & set(re.findall(r"(?i)\b(?:from|join|update|into)\s+([\w.]+)", document.text)))
        if table_overlap:
            evidence.append("SQL tables: " + ", ".join(table_overlap))
        # Different datasets with merely similar prose are deliberately visible but not blocking.
        score = min(1.0, 0.55 * bool(shared_datasets) + 0.25 * similarity + 0.1 * bool(keyword_overlap) + 0.2 * bool(table_overlap))
        if evidence:
            candidates.append(DocumentConflictCandidate(
                document_urn=document.urn, title=document.title, related_dataset_urns=document.related_dataset_urns,
                score=score, evidence=evidence, detector_version=DETECTOR_VERSION, detected_at=detected_at,
                high_risk=bool(shared_datasets) and high_risk_source,
            ))
    return sorted(candidates, key=lambda item: (-item.score, item.document_urn))
