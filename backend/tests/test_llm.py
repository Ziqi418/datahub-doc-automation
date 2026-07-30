from __future__ import annotations

import pytest

from document_enrichment.recommendation.llm import (
    FakeLLMProvider,
    LLMItem,
    LLMResponse,
    ProviderError,
    recommend_with_llm,
)
from document_enrichment.recommendation.rules import recommend_rules


@pytest.mark.asyncio
async def test_llm_merges_only_whitelisted_urns(catalog) -> None:
    rules = recommend_rules("# Revenue\n```sql\nselect * from fct_orders\n```", "revenue.md", catalog)
    provider = FakeLLMProvider(
        LLMResponse(
            domain=LLMItem(urn="urn:li:domain:finance", score=0.9, reason="Revenue reporting"),
            datasets=[LLMItem(urn=rules.datasets[0].urn, score=0.8, reason="SQL table reference")],
        )
    )
    result = await recommend_with_llm(provider=provider, text="revenue", catalog=catalog, rule_recommendations=rules)
    assert result.datasets[0].source == "rule_and_llm"
    assert result.datasets[0].confidence > 0


@pytest.mark.asyncio
async def test_llm_rejects_out_of_candidate_dataset_and_retries(catalog) -> None:
    rules = recommend_rules("# Revenue\n```sql\nselect * from fct_orders\n```", "revenue.md", catalog)
    provider = FakeLLMProvider(
        LLMResponse(datasets=[LLMItem(urn="urn:li:dataset:(urn:li:dataPlatform:nope,x,PROD)", score=1, reason="bad")])
    )
    with pytest.raises(ProviderError, match="after one repair retry"):
        await recommend_with_llm(provider=provider, text="revenue", catalog=catalog, rule_recommendations=rules)
    assert provider.calls == 2
