from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from document_enrichment.extraction.document import ExtractedDocument, extract_document
from document_enrichment.models import (
    CatalogSnapshot,
    Dataset,
    Evidence,
    Recommendation,
    RecommendationSet,
)


@dataclass
class _Candidate:
    dataset: Dataset
    score: float = 0
    evidence: list[Evidence] | None = None

    def __post_init__(self) -> None:
        self.evidence = self.evidence or []

    def add(self, score: float, evidence: Evidence) -> None:
        self.score += score
        self.evidence.append(evidence)


def recommend_rules(text: str, filename: str, catalog: CatalogSnapshot) -> RecommendationSet:
    extracted = extract_document(text, filename)
    candidates = _score_datasets(extracted, catalog.datasets)
    datasets = _dataset_recommendations(candidates)
    return RecommendationSet(
        domain=_supported_entity(
            datasets, catalog.domains, {d.urn: d.domain_urn for d in catalog.datasets}, "domain"
        ),
        tags=_supported_entities(
            datasets, catalog.tags, {d.urn: d.tag_urns for d in catalog.datasets}, "tag"
        ),
        owner=_supported_entity(
            datasets, catalog.owners, {d.urn: d.owner_urns for d in catalog.datasets}, "owner"
        ),
        datasets=datasets[:5],
        provider="rule",
    )


def dataset_candidates(
    text: str, filename: str, catalog: CatalogSnapshot, limit: int = 30
) -> list[Recommendation]:
    return _dataset_recommendations(
        _score_datasets(extract_document(text, filename), catalog.datasets)
    )[:limit]


def merge_dataset_candidates(
    deterministic: list[Recommendation], keyword_datasets: list[Dataset], *, limit: int = 30
) -> list[Recommendation]:
    """Stable union that never lets fuzzy keyword matches displace SQL anchors."""
    merged = {item.urn: item.model_copy(deep=True) for item in deterministic}
    for dataset in keyword_datasets:
        keyword_evidence = Evidence(
            kind="datahub_keyword_search",
            matched_text=dataset.qualified_name,
            location="DataHub native keyword search",
        )
        if dataset.urn in merged:
            merged[dataset.urn].evidence.append(keyword_evidence)
            continue
        merged[dataset.urn] = Recommendation(
            urn=dataset.urn,
            display_name=dataset.qualified_name,
            confidence=0.2,
            reason="Returned by DataHub keyword search.",
            evidence=[keyword_evidence],
            source="datahub_keyword",
        )

    def order(item: Recommendation) -> tuple[int, float, str]:
        anchor = any(e.kind == "sql_table_reference" for e in item.evidence)
        return (0 if anchor else 1, -item.confidence, item.urn)

    return sorted(merged.values(), key=order)[:limit]


def _score_datasets(extracted: ExtractedDocument, datasets: list[Dataset]) -> list[_Candidate]:
    candidates: dict[str, _Candidate] = {dataset.urn: _Candidate(dataset) for dataset in datasets}
    lower_body = extracted.body.casefold()
    short_name_counts = Counter(dataset.name.casefold() for dataset in datasets)
    for candidate in candidates.values():
        dataset = candidate.dataset
        names = {dataset.name.casefold(), dataset.qualified_name.casefold()}
        for reference in extracted.table_references:
            normalized = reference.text.casefold()
            if normalized in names or normalized.rsplit(".", 1)[-1] in names:
                candidate.add(
                    100,
                    Evidence(
                        kind="sql_table_reference",
                        matched_text=reference.text,
                        location=reference.location,
                    ),
                )
        qualified = dataset.qualified_name.casefold()
        if qualified and _contains_exact_name(lower_body, qualified):
            candidate.add(
                90,
                Evidence(
                    kind="exact_dataset_name",
                    matched_text=dataset.qualified_name,
                    location="document body",
                ),
            )
        short = dataset.name.casefold()
        if short and short in extracted.tokens:
            # A short name collision must remain as multiple candidates; do not choose one silently.
            weight = 45 if short_name_counts[short] == 1 else 25
            candidate.add(
                weight,
                Evidence(
                    kind="exact_dataset_name", matched_text=dataset.name, location="document body"
                ),
            )
    # Include direct SQL matches even when otherwise tied, then preserve a deterministic order.
    return sorted(candidates.values(), key=lambda item: (-item.score, item.dataset.urn))


def _contains_exact_name(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", text))


def _dataset_recommendations(candidates: list[_Candidate]) -> list[Recommendation]:
    active = [candidate for candidate in candidates if candidate.score > 0]
    if not active:
        return []
    maximum = active[0].score
    recommendations: list[Recommendation] = []
    for candidate in active:
        sql_exact = any(
            evidence.kind == "sql_table_reference" for evidence in candidate.evidence or []
        )
        confidence = 0.99 if sql_exact else min(0.94, max(0.05, 0.65 * candidate.score / maximum))
        evidence = candidate.evidence or []
        if evidence:
            reason = _reason(evidence[0])
        else:
            reason = "Lexical metadata match."
        recommendations.append(
            Recommendation(
                urn=candidate.dataset.urn,
                display_name=candidate.dataset.qualified_name,
                confidence=round(confidence, 3),
                reason=reason,
                evidence=evidence,
                source="rule",
            )
        )
    return recommendations


def _reason(evidence: Evidence) -> str:
    messages = {
        "sql_table_reference": f"SQL references {evidence.matched_text}.",
        "exact_dataset_name": f"Document explicitly mentions {evidence.matched_text}.",
    }
    return messages.get(evidence.kind, "Rule-based metadata match.")


def _supported_entity(
    datasets: list[Recommendation], entities: list, associations: dict, entity_kind: str
) -> Recommendation | None:
    items = _supported_entities(datasets, entities, associations, entity_kind)
    return items[0] if items else None


def _supported_entities(
    datasets: list[Recommendation], entities: list, associations: dict, entity_kind: str
) -> list[Recommendation]:
    entity_by_urn = {entity.urn: entity for entity in entities}
    support: dict[str, float] = defaultdict(float)
    for dataset in datasets:
        relation = associations.get(dataset.urn)
        related_urns = relation if isinstance(relation, list) else [relation]
        for urn in related_urns:
            if urn in entity_by_urn:
                support[urn] += dataset.confidence
    ordered = sorted(support, key=lambda urn: (-support[urn], urn))
    return [
        Recommendation(
            urn=urn,
            display_name=entity_by_urn[urn].name,
            confidence=round(min(0.95, support[urn]), 3),
            reason=f"Supported by related dataset metadata ({entity_kind}).",
            evidence=[
                Evidence(
                    kind=f"related_dataset_{entity_kind}",
                    matched_text=urn,
                    location="related datasets",
                )
            ],
            source="rule",
        )
        for urn in ordered[:5]
    ]
