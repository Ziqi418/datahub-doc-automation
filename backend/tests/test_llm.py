import pytest

from document_enrichment.models import (
    CatalogSnapshot,
    Dataset,
    Domain,
    Owner,
    Recommendation,
    RecommendationSet,
    Tag,
)
from document_enrichment.recommendation.llm import (
    FakeLLMProvider,
    LLMItem,
    LLMResponse,
    ProviderError,
    recommend_with_llm,
)


@pytest.fixture
def catalog() -> CatalogSnapshot:
    dataset = Dataset(
        urn="urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,daily_sales,PROD)",
        name="daily_sales",
        qualified_name="daily_sales",
    )
    return CatalogSnapshot(
        domains=[Domain(urn="urn:li:domain:operations", name="Operations")],
        tags=[Tag(urn="urn:li:tag:sales", name="sales")],
        owners=[Owner(urn="urn:li:corpuser:analytics", name="Analytics", owner_type="CORP_USER")],
        datasets=[dataset],
    )


@pytest.fixture
def rules(catalog: CatalogSnapshot) -> RecommendationSet:
    dataset = catalog.datasets[0]
    return RecommendationSet(
        domain=Recommendation(
            urn=catalog.domains[0].urn,
            display_name="Operations",
            confidence=0.8,
            reason="rule",
            source="rule",
        ),
        tags=[
            Recommendation(
                urn=catalog.tags[0].urn,
                display_name="sales",
                confidence=0.8,
                reason="rule",
                source="rule",
            )
        ],
        owner=Recommendation(
            urn=catalog.owners[0].urn,
            display_name="Analytics",
            confidence=0.8,
            reason="rule",
            source="rule",
        ),
        datasets=[
            Recommendation(
                urn=dataset.urn,
                display_name="daily_sales",
                confidence=0.9,
                reason="rule",
                source="rule",
            )
        ],
    )


def _response(dataset_urn: str) -> LLMResponse:
    return LLMResponse(
        domain=LLMItem(urn="urn:li:domain:operations", score=0.9, reason="domain"),
        tags=[LLMItem(urn="urn:li:tag:sales", score=0.9, reason="tag")],
        owner=LLMItem(urn="urn:li:corpuser:analytics", score=0.9, reason="owner"),
        datasets=[LLMItem(urn=dataset_urn, score=0.9, reason="dataset")],
    )


@pytest.mark.asyncio
async def test_llm_returns_all_recommendation_types(
    catalog: CatalogSnapshot, rules: RecommendationSet
) -> None:
    result = await recommend_with_llm(
        provider=FakeLLMProvider(_response(catalog.datasets[0].urn)),
        text="daily sales",
        catalog=catalog,
        rule_recommendations=rules,
    )
    assert result.domain and result.owner
    assert len(result.tags) == len(result.datasets) == 1


@pytest.mark.asyncio
async def test_llm_rejects_urn_outside_candidate_whitelist(
    catalog: CatalogSnapshot, rules: RecommendationSet
) -> None:
    provider = FakeLLMProvider(
        _response("urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,secret,PROD)")
    )
    with pytest.raises(ProviderError, match="repair retry"):
        await recommend_with_llm(
            provider=provider,
            text="daily sales",
            catalog=catalog,
            rule_recommendations=rules,
        )
    assert provider.calls == 2
