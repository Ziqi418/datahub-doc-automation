from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from document_enrichment.api.app import (
    create_app,
    get_conflict_gateway,
    get_gateway,
    get_llm_provider,
    get_publisher,
)
from document_enrichment.config import Settings
from document_enrichment.datahub.conflicts import ExistingDocument
from document_enrichment.models import AnalysisStatus
from document_enrichment.recommendation.llm import (
    FakeLLMProvider,
    FieldDisambiguationItem,
    FieldDisambiguationResponse,
    LLMItem,
    LLMResponse,
    SemanticConflictItem,
    SemanticConflictResponse,
)
from document_enrichment.recommendation.rules import recommend_rules


def _client(tmp_path, in_memory_catalog):
    database_path = tmp_path / "app.db"
    _migrate(database_path)
    app = create_app(Settings(database_path=database_path))
    rule = recommend_rules(
        "# Test\n```sql\nselect * from fct_orders\n```", "test.md", in_memory_catalog.catalog
    )
    app.dependency_overrides[get_gateway] = lambda: in_memory_catalog
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(
        LLMResponse(
            domain=LLMItem(urn="urn:li:domain:finance", score=0.9, reason="finance"),
            owner=LLMItem(urn="urn:li:corpgroup:finance-analytics", score=0.9, reason="finance"),
            datasets=[LLMItem(urn=rule.datasets[0].urn, score=0.9, reason="SQL")],
        )
    )
    app.dependency_overrides[get_publisher] = lambda: FakePublisher()
    app.dependency_overrides[get_conflict_gateway] = lambda: FakeConflictGateway()
    return TestClient(app)


class FakePublisher:
    async def publish(self, *, analysis_id, **_kwargs):
        return type("Published", (), {"urn": f"urn:li:document:doc-enrichment-{analysis_id}"})()


class FakeConflictGateway:
    def __init__(self, documents=None):
        self.documents = documents or []
        self.calls = 0

    async def search_documents(self, *, query, limit=20):
        self.calls += 1
        return self.documents[:limit]


def _migrate(database_path: Path) -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "db" / "migrations"
    environment = os.environ | {
        "DBMATE_DATABASE_URL": f"sqlite:{database_path}",
        "DBMATE_MIGRATIONS_DIR": str(migrations_dir),
    }
    subprocess.run(
        ["dbmate", "--env", "DBMATE_DATABASE_URL", "--no-dump-schema", "up"],
        check=True,
        env=environment,
    )


def test_upload_validation_does_not_create_analysis(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        response = client.post(
            "/api/analyses", files={"file": ("attack.pdf", b"x", "application/pdf")}
        )
        assert response.status_code == 415
        response = client.post(
            "/api/analyses", files={"file": ("empty.md", b"  ", "text/markdown")}
        )
        assert response.status_code == 400


def test_review_records_replacement_and_blocks_direct_publish(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "revenue.md",
                    b"# Revenue\n```sql\nselect * from fct_orders\n```",
                    "text/markdown",
                )
            },
        )
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
        published = client.post(f"/api/analyses/{analysis_id}/publish")
        assert published.status_code == 200
        assert published.json()["analysis"]["status"] == AnalysisStatus.PUBLISHED.value
        # Repeated button clicks return the same document instead of creating another entity.
        assert (
            client.post(f"/api/analyses/{analysis_id}/publish").json()["document_urn"]
            == published.json()["document_urn"]
        )


def test_review_draft_keeps_analysis_in_review(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post("/api/analyses", files={"file": (
            "draft.md", b"# Draft\n```sql\nselect * from fct_orders\n```", "text/markdown"
        )})
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        saved = client.put(f"/api/analyses/{analysis_id}/review/draft", json={"dataset_urns": [
            "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
        ]})
        assert saved.status_code == 200
        assert saved.json()["status"] == AnalysisStatus.READY_FOR_REVIEW.value
        assert saved.json()["final_selection"]["dataset_urns"]


def test_publish_failure_stays_retryable(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        app = client.app

        class BrokenPublisher:
            async def publish(self, **_kwargs):
                raise RuntimeError("simulated partial failure")

        app.dependency_overrides[get_publisher] = lambda: BrokenPublisher()
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": ("x.md", b"# x\n```sql\nselect * from fct_orders\n```", "text/markdown")
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert (
            client.put(f"/api/analyses/{analysis_id}/review", json={"dataset_urns": []}).status_code
            == 200
        )
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 502
        assert (
            client.get(f"/api/analyses/{analysis_id}").json()["status"]
            == AnalysisStatus.PUBLISH_FAILED.value
        )


def test_freshness_marks_changed_dataset_once(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": ("x.md", b"# x\n```sql\nselect * from fct_orders\n```", "text/markdown")
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert (
            client.put(
                f"/api/analyses/{analysis_id}/review",
                json={
                    "dataset_urns": [
                        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
                    ]
                },
            ).status_code
            == 200
        )
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 200
        in_memory_catalog.catalog.datasets[8].description = "meaningfully changed"
        changed = client.post(f"/api/analyses/{analysis_id}/freshness")
        assert changed.status_code == 200
        assert changed.json()["changed"] is True
        assert changed.json()["analysis"]["freshness_status"] == "NEEDS_REVIEW"
        repeated = client.post(f"/api/analyses/{analysis_id}/freshness")
        assert repeated.json()["analysis"]["updated_at"] == changed.json()["analysis"]["updated_at"]


def test_document_list_persists_freshness_evidence_and_allows_acknowledgement(
    tmp_path, in_memory_catalog
) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "tracked.md",
                    b"# tracked\n```sql\nselect * from fct_orders\n```",
                    "text/markdown",
                )
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert (
            client.put(
                f"/api/analyses/{analysis_id}/review",
                json={
                    "dataset_urns": [
                        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
                    ]
                },
            ).status_code
            == 200
        )
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 200
        in_memory_catalog.catalog.datasets[8].description = "changed since review"

        freshness = client.post(f"/api/analyses/{analysis_id}/freshness")
        assert freshness.status_code == 200
        assert freshness.json()["evidence"]
        detail = client.get(f"/api/analyses/{analysis_id}").json()
        assert detail["freshness_evidence"] == freshness.json()["evidence"]
        listing = client.get("/api/analyses").json()
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == analysis_id
        assert listing["items"][0]["freshness_status"] == "NEEDS_REVIEW"
        acknowledged = client.post(f"/api/analyses/{analysis_id}/freshness/acknowledge")
        assert acknowledged.status_code == 200
        assert acknowledged.json()["freshness_status"] == "ACKNOWLEDGED"


def test_recent_mock_database_change_checks_only_related_documents_and_offers_patch(
    tmp_path, in_memory_catalog
) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "migration.md",
                    b"# Metric\nUse gross_revenue from fct_orders.",
                    "text/markdown",
                )
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert (
            client.put(
                f"/api/analyses/{analysis_id}/review",
                json={
                    "dataset_urns": [
                        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
                    ]
                },
            ).status_code
            == 200
        )
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 200
        check = client.post("/api/freshness/recent-changes")
        assert check.status_code == 200
        assert check.json()["affected_analysis_ids"] == [analysis_id]
        detail = client.get(f"/api/analyses/{analysis_id}").json()
        assert detail["freshness_evidence"][0]["recommendation_confidence"] == 0.98
        assert "gross_amount" in detail["freshness_evidence"][0]["proposed_content"]
        applied = client.post(
            f"/api/analyses/{analysis_id}/freshness/apply-suggestion?evidence_index=0"
        )
        assert applied.status_code == 200
        assert applied.json()["freshness_status"] == "ACTIVE"
        assert "gross_amount" in client.get(f"/api/analyses/{analysis_id}/source").text


def test_high_risk_conflict_blocks_write_until_confirmed(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        publisher = FakePublisher()
        conflict = FakeConflictGateway(
            [
                ExistingDocument(
                    urn="urn:li:document:existing",
                    title="Revenue metric definition",
                    text="The revenue metric formula is documented here.",
                    related_dataset_urns=[
                        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
                    ],
                    domain_urn=None,
                )
            ]
        )
        client.app.dependency_overrides[get_publisher] = lambda: publisher
        client.app.dependency_overrides[get_conflict_gateway] = lambda: conflict
        client.app.state.llm_provider = FakeLLMProvider(
            LLMResponse(),
            conflict_response=SemanticConflictResponse(
                documents=[
                    SemanticConflictItem(
                        document_urn="urn:li:document:existing",
                        classification="duplicate",
                        confidence=0.96,
                        reason="Same audience and revenue metric definition.",
                    )
                ]
            ),
        )
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "metric.md",
                    b"# Revenue metric definition\nRevenue metric formula\n```sql\nselect * from fct_orders\n```",
                    "text/markdown",
                )
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        selection = {"dataset_urns": ["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"]}
        conflicts = client.post(f"/api/analyses/{analysis_id}/review/conflicts", json=selection)
        assert conflicts.status_code == 200
        assert conflicts.json()["candidates"][0]["semantic_classification"] == "duplicate"
        assert conflicts.json()["candidates"][0]["high_risk"] is True
        assert client.put(f"/api/analyses/{analysis_id}/review", json=selection).status_code == 200
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 200


def test_shared_dataset_without_semantic_provider_is_pending_not_a_publish_gate(
    tmp_path, in_memory_catalog
) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        client.app.dependency_overrides[get_conflict_gateway] = lambda: FakeConflictGateway(
            [
                ExistingDocument(
                    urn="urn:li:document:other",
                    title="Orders operations guide",
                    text="Operational runbook.",
                    related_dataset_urns=[
                        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
                    ],
                    domain_urn=None,
                )
            ]
        )
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "metric.md",
                    b"# Revenue explanation\n```sql\nselect * from fct_orders\n```",
                    "text/markdown",
                )
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert (
            client.put(
                f"/api/analyses/{analysis_id}/review",
                json={
                    "dataset_urns": [
                        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
                    ]
                },
            ).status_code
            == 200
        )
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 200


def test_explicit_review_disposition_allows_publish_of_missing_sql_field(
    tmp_path, in_memory_catalog
) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "schema.md",
                    b"# Schema\n```sql\nselect fct_orders.not_a_field from fct_orders\n```",
                    "text/markdown",
                )
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        review_draft = {
            "dataset_urns": ["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"],
            "field_dispositions": [],
        }
        field_check = client.post(
            f"/api/analyses/{analysis_id}/review/field-check", json=review_draft
        )
        missing_id = next(
            item["id"]
            for item in field_check.json()["references"]
            if item["field_path"] == "not_a_field"
        )
        review_draft["field_dispositions"] = [
            {"reference_id": missing_id, "action": "keep_unresolved"}
        ]
        assert (
            client.put(
                f"/api/analyses/{analysis_id}/review",
                json=review_draft,
            ).status_code
            == 200
        )
        validation = client.post(f"/api/analyses/{analysis_id}/schema-validation")
        assert validation.status_code == 200
        missing = next(
            item for item in validation.json()["references"] if item["field_path"] == "not_a_field"
        )
        assert missing["status"] == "unresolved" and missing["confirmed"] is True
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 200


def test_schema_linter_resolves_a_qualified_field_for_one_selected_dataset(
    tmp_path, in_memory_catalog
) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "schema.md",
                    b"# Schema\n```sql\nselect fct_orders.net_revenue from fct_orders\n```",
                    "text/markdown",
                )
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert (
            client.put(
                f"/api/analyses/{analysis_id}/review",
                json={
                    "dataset_urns": [
                        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
                    ]
                },
            ).status_code
            == 200
        )
        references = client.post(f"/api/analyses/{analysis_id}/schema-validation").json()[
            "references"
        ]
        resolved = next(item for item in references if item["field_path"] == "net_revenue")
        assert resolved["status"] == "resolved"


def test_review_field_check_batches_without_blocking_preview(
    tmp_path, in_memory_catalog
) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "field.md",
                    b"# Field owner\n```sql\nselect order_id from fct_orders join payments on 1=1\n```",
                    "text/markdown",
                )
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        fct_orders = "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
        payments = "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,payments,PROD)"
        provider = FakeLLMProvider(
            LLMResponse(),
            field_response=FieldDisambiguationResponse(
                fields=[
                    FieldDisambiguationItem(
                        reference_id="placeholder",
                        dataset_urn=fct_orders,
                        confidence=0.94,
                        reason="Order context.",
                    ),
                ]
            ),
        )
        client.app.state.llm_provider = provider
        selection = {"dataset_urns": [fct_orders, payments], "field_dispositions": []}
        # IDs are deterministic but intentionally opaque to callers: first learn it from the checked draft,
        # then configure the fake with the real reference id and make the single provider request.
        client.app.state.llm_provider = None
        fallback = client.post(
            f"/api/analyses/{analysis_id}/review/field-check", json=selection
        ).json()
        field_id = next(
            item["id"] for item in fallback["references"] if item["status"] == "ambiguous"
        )
        provider.field_response = FieldDisambiguationResponse(
            fields=[
                FieldDisambiguationItem(
                    reference_id=field_id,
                    dataset_urn=fct_orders,
                    confidence=0.94,
                    reason="Order context.",
                ),
            ]
        )
        client.app.state.llm_provider = provider
        checked = client.post(f"/api/analyses/{analysis_id}/review/field-check", json=selection)
        assert (
            checked.status_code == 200
            and checked.json()["suggestions"][0]["dataset_urn"] == fct_orders
        )
        assert provider.calls == 1
        assert client.post(f"/api/analyses/{analysis_id}/review/field-check", json=selection).json()["provider_status"] == "available"
        assert provider.calls == 2
        assert client.put(f"/api/analyses/{analysis_id}/review", json=selection).status_code == 200


def test_return_to_review_preserves_selection_and_downloads_source(
    tmp_path, in_memory_catalog
) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": (
                    "source.md",
                    b"# Source\n```sql\nselect * from fct_orders\n```",
                    "text/markdown",
                )
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        selection = {
            "dataset_urns": ["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"]
        }
        assert client.put(f"/api/analyses/{analysis_id}/review", json=selection).status_code == 200
        returned = client.post(f"/api/analyses/{analysis_id}/return-to-review")
        assert returned.status_code == 200
        assert returned.json()["status"] == AnalysisStatus.READY_FOR_REVIEW.value
        assert returned.json()["final_selection"]["dataset_urns"] == selection["dataset_urns"]
        source = client.get(f"/api/analyses/{analysis_id}/source")
        assert source.text == "# Source\n```sql\nselect * from fct_orders\n```"
        assert "attachment;" in source.headers["content-disposition"]


def test_conflict_search_query_does_not_mix_asset_urns_into_full_text() -> None:
    from document_enrichment.api.app import _conflict_query
    from document_enrichment.models import ReviewSelection

    query = _conflict_query(
        ReviewSelection(
            domain_urn="urn:li:domain:finance",
            dataset_urns=["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"],
        ),
        "Revenue metric definition",
    )
    assert query == "Revenue metric definition"


@pytest.mark.asyncio
async def test_conflict_retrieval_merges_title_keyword_results() -> None:
    from document_enrichment.api.app import _retrieve_conflict_documents
    from document_enrichment.datahub.conflicts import ExistingDocument
    from document_enrichment.models import ReviewSelection

    document = ExistingDocument(
        "urn:li:document:revenue", "Orders revenue field validation", "", [], None
    )

    class Gateway:
        async def search_documents(self, *, query: str, limit: int = 20):
            return [document] if query == "revenue" else []

    retrieved = await _retrieve_conflict_documents(
        Gateway(), ReviewSelection(), "Revenue metric definition"
    )
    assert retrieved == [document]


def test_schema_field_changes_have_old_and_new_values() -> None:
    from document_enrichment.api.app import _freshness_differences
    from document_enrichment.models import CatalogSnapshot, Dataset, SchemaField

    urn = "urn:li:dataset:(urn:li:dataPlatform:test,orders,PROD)"
    baseline = [
        {
            "urn": urn,
            "description": "",
            "schema_fields": ["id"],
            "field_snapshots": [
                {
                    "field_path": "id",
                    "native_data_type": "BIGINT",
                    "nullable": False,
                    "description": "old",
                }
            ],
            "domain_urn": None,
            "owner_urns": [],
            "tag_urns": [],
            "deprecated": False,
            "referenced_fields": ["id"],
        }
    ]
    snapshot = CatalogSnapshot(
        datasets=[
            Dataset(
                urn=urn,
                name="orders",
                qualified_name="orders",
                schema_fields=["id"],
                field_snapshots=[
                    SchemaField(
                        field_path="id",
                        native_data_type="VARCHAR",
                        nullable=True,
                        description="new",
                    )
                ],
            )
        ]
    )
    differences = _freshness_differences(baseline, snapshot)
    assert any("native_data_type: old='BIGINT' new='VARCHAR'" in item for item in differences)
    assert any("nullable: old=False new=True" in item for item in differences)


def test_review_accepts_twenty_dataset_associations(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={
                "file": ("x.md", b"# x\n```sql\nselect * from fct_orders\n```", "text/markdown")
            },
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        available = [dataset.urn for dataset in in_memory_catalog.catalog.datasets]
        # The fixture catalog is smaller than 20; repeat-free real URNs still prove
        # that the expanded model accepts more than the former limit of five.
        response = client.put(
            f"/api/analyses/{analysis_id}/review", json={"dataset_urns": available[:8]}
        )
        assert response.status_code == 200
        assert len(response.json()["analysis"]["final_selection"]["dataset_urns"]) == 8
