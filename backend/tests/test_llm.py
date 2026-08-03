import json

import httpx
import pytest

from document_enrichment.config import Settings
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
    OpenAICompatibleProvider,
    ProviderError,
    _response_json_schema,
    recommend_with_llm,
)


@pytest.fixture
def catalog() -> CatalogSnapshot:
    return CatalogSnapshot(
        domains=[Domain(urn="urn:li:domain:operations", name="Operations")],
        tags=[Tag(urn="urn:li:tag:sales", name="sales")],
        owners=[Owner(urn="urn:li:corpuser:analytics", name="Analytics", owner_type="CORP_USER")],
        datasets=[
            Dataset(
                urn="urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,daily_sales,PROD)",
                name="daily_sales",
                qualified_name="daily_sales",
            )
        ],
    )


@pytest.fixture
def rules(catalog: CatalogSnapshot) -> RecommendationSet:
    dataset = catalog.datasets[0]
    return RecommendationSet(
        domain=Recommendation(
            urn="urn:li:domain:operations", display_name="Operations", confidence=0.8, reason="rule", source="rule"
        ),
        tags=[Recommendation(urn="urn:li:tag:sales", display_name="sales", confidence=0.8, reason="rule", source="rule")],
        owner=Recommendation(
            urn="urn:li:corpuser:analytics", display_name="Analytics", confidence=0.8, reason="rule", source="rule"
        ),
        datasets=[Recommendation(urn=dataset.urn, display_name="daily_sales", confidence=0.9, reason="rule", source="rule")],
    )


def _llm_response(dataset_urn: str) -> LLMResponse:
    item = LLMItem(urn=dataset_urn, score=0.9, reason="Matches the daily sales dashboard.")
    return LLMResponse(
        domain=LLMItem(urn="urn:li:domain:operations", score=0.9, reason="Operational dashboard."),
        tags=[LLMItem(urn="urn:li:tag:sales", score=0.9, reason="Sales metrics.")],
        owner=LLMItem(urn="urn:li:corpuser:analytics", score=0.9, reason="Owns the metrics."),
        datasets=[item],
    )


@pytest.mark.asyncio
async def test_provider_uses_responses_api_and_parses_structured_output(catalog: CatalogSnapshot) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        body = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": _llm_response(catalog.datasets[0].urn).model_dump_json()}],
                }
            ],
        }
        return httpx.Response(200, json=body, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        Settings(
            llm_api_key="test-key",
            llm_model="test-model",
            llm_base_url="https://example.test/v1",
            llm_reasoning_effort="none",
        ),
        client,
    )
    result = await provider.rank(document="daily sales", candidates={"datasets": []})
    await client.aclose()

    assert result.datasets[0].urn == catalog.datasets[0].urn
    assert captured["url"] == "https://example.test/v1/responses"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 8_000
    assert payload["reasoning"] == {"effort": "none"}
    assert isinstance(payload["input"], str)
    assert "instructions" in payload and "messages" not in payload
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"] == _response_json_schema()


@pytest.mark.asyncio
async def test_recommend_with_llm_returns_all_four_recommendation_types(
    catalog: CatalogSnapshot, rules: RecommendationSet
) -> None:
    provider = FakeLLMProvider(_llm_response(catalog.datasets[0].urn))

    result = await recommend_with_llm(
        provider=provider, text="daily sales dashboard", catalog=catalog, rule_recommendations=rules
    )

    assert result.provider == "fake"
    assert result.domain and result.domain.urn == "urn:li:domain:operations"
    assert [tag.urn for tag in result.tags] == ["urn:li:tag:sales"]
    assert result.owner and result.owner.urn == "urn:li:corpuser:analytics"
    assert [dataset.urn for dataset in result.datasets] == [catalog.datasets[0].urn]


@pytest.mark.asyncio
async def test_recommend_with_llm_rejects_urn_outside_candidate_whitelist(
    catalog: CatalogSnapshot, rules: RecommendationSet
) -> None:
    response = _llm_response("urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,secret,PROD)")
    provider = FakeLLMProvider(response)

    with pytest.raises(ProviderError, match="repair retry"):
        await recommend_with_llm(provider=provider, text="daily sales", catalog=catalog, rule_recommendations=rules)

    assert provider.calls == 2


@pytest.mark.asyncio
async def test_provider_rejects_invalid_json_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "nope"}]}]},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        Settings(llm_api_key="test-key", llm_model="test-model", llm_base_url="https://example.test/v1"), client
    )

    with pytest.raises(ProviderError, match="invalid"):
        await provider.rank(document="text", candidates={})
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_accepts_complete_output_text_from_incomplete_response(catalog: CatalogSnapshot) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": _llm_response(catalog.datasets[0].urn).model_dump_json()}
                        ],
                    }
                ],
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        Settings(llm_api_key="test-key", llm_model="test-model", llm_base_url="https://example.test/v1"), client
    )

    result = await provider.rank(document="text", candidates={})

    assert result.datasets[0].urn == catalog.datasets[0].urn
    await client.aclose()
