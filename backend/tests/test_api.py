from __future__ import annotations

from fastapi.testclient import TestClient

from document_enrichment.api.app import create_app, get_gateway, get_llm_provider
from document_enrichment.config import Settings
from document_enrichment.models import AnalysisStatus
from document_enrichment.recommendation.llm import FakeLLMProvider, LLMItem, LLMResponse
from document_enrichment.recommendation.rules import recommend_rules


def _client(tmp_path, in_memory_catalog):
    app = create_app(Settings(database_path=tmp_path / "app.db"))
    rule = recommend_rules("# Test\n```sql\nselect * from fct_orders\n```", "test.md", in_memory_catalog.catalog)
    app.dependency_overrides[get_gateway] = lambda: in_memory_catalog
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(
        LLMResponse(
            domain=LLMItem(urn="urn:li:domain:finance", score=0.9, reason="finance"),
            owner=LLMItem(urn="urn:li:corpgroup:finance-analytics", score=0.9, reason="finance"),
            datasets=[LLMItem(urn=rule.datasets[0].urn, score=0.9, reason="SQL")],
        )
    )
    return TestClient(app)


def test_upload_validation_does_not_create_analysis(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        response = client.post("/api/analyses", files={"file": ("attack.pdf", b"x", "application/pdf")})
        assert response.status_code == 415
        response = client.post("/api/analyses", files={"file": ("empty.md", b"  ", "text/markdown")})
        assert response.status_code == 400


def test_review_records_replacement_and_blocks_direct_publish(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post("/api/analyses", files={"file": ("revenue.md", b"# Revenue\n```sql\nselect * from fct_orders\n```", "text/markdown")})
        assert uploaded.status_code == 201
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        response = client.put(
            f"/api/analyses/{analysis_id}/review",
            json={
                "domain_urn": "urn:li:domain:finance",
                "owner_urn": "urn:li:corpgroup:finance-analytics",
                "tag_urns": ["urn:li:tag:revenue"],
                "dataset_urns": ["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,payments,PROD)"],
            },
        )
        assert response.status_code == 200
        assert response.json()["analysis"]["status"] == AnalysisStatus.APPROVED.value
        assert any(action["action"] == "replaced" for action in response.json()["actions"])
        # Publishing is deliberately absent until Phase 7, so no endpoint can bypass review.
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 404
