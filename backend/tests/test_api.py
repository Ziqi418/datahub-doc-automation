from __future__ import annotations

import os
import subprocess
from pathlib import Path

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
from document_enrichment.recommendation.llm import FakeLLMProvider, LLMItem, LLMResponse
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
        assert client.post(f"/api/analyses/{analysis_id}/publish").json()["document_urn"] == published.json()["document_urn"]


def test_publish_failure_stays_retryable(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        app = client.app

        class BrokenPublisher:
            async def publish(self, **_kwargs):
                raise RuntimeError("simulated partial failure")

        app.dependency_overrides[get_publisher] = lambda: BrokenPublisher()
        uploaded = client.post(
            "/api/analyses",
            files={"file": ("x.md", b"# x\n```sql\nselect * from fct_orders\n```", "text/markdown")},
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert client.put(f"/api/analyses/{analysis_id}/review", json={"dataset_urns": []}).status_code == 200
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 502
        assert client.get(f"/api/analyses/{analysis_id}").json()["status"] == AnalysisStatus.PUBLISH_FAILED.value


def test_freshness_marks_changed_dataset_once(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses",
            files={"file": ("x.md", b"# x\n```sql\nselect * from fct_orders\n```", "text/markdown")},
        )
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert client.put(
            f"/api/analyses/{analysis_id}/review",
            json={"dataset_urns": ["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"]},
        ).status_code == 200
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 200
        in_memory_catalog.catalog.datasets[8].description = "meaningfully changed"
        changed = client.post(f"/api/analyses/{analysis_id}/freshness")
        assert changed.status_code == 200
        assert changed.json()["changed"] is True
        assert changed.json()["analysis"]["freshness_status"] == "NEEDS_REVIEW"
        repeated = client.post(f"/api/analyses/{analysis_id}/freshness")
        assert repeated.json()["analysis"]["updated_at"] == changed.json()["analysis"]["updated_at"]


def test_high_risk_conflict_blocks_write_until_confirmed(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        publisher = FakePublisher()
        conflict = FakeConflictGateway([ExistingDocument(
            urn="urn:li:document:existing", title="Revenue metric definition",
            text="The revenue metric formula is documented here.",
            related_dataset_urns=["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"], domain_urn=None,
        )])
        client.app.dependency_overrides[get_publisher] = lambda: publisher
        client.app.dependency_overrides[get_conflict_gateway] = lambda: conflict
        uploaded = client.post("/api/analyses", files={"file": (
            "metric.md", b"# Revenue metric definition\nRevenue metric formula\n```sql\nselect * from fct_orders\n```", "text/markdown"
        )})
        analysis_id = uploaded.json()["analysis"]["id"]
        assert client.post(f"/api/analyses/{analysis_id}/recommend").status_code == 200
        assert client.put(f"/api/analyses/{analysis_id}/review", json={"dataset_urns": [
            "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
        ]}).status_code == 200
        blocked = client.post(f"/api/analyses/{analysis_id}/publish")
        assert blocked.status_code == 409
        conflicts = client.get(f"/api/analyses/{analysis_id}/conflicts").json()["candidates"]
        assert conflicts[0]["document_urn"] == "urn:li:document:existing"
        assert conflicts[0]["confirmed"] is False
        assert client.put(f"/api/analyses/{analysis_id}/conflicts/urn:li:document:existing/confirm").status_code == 200
        assert client.post(f"/api/analyses/{analysis_id}/publish").status_code == 200


def test_schema_field_changes_have_old_and_new_values() -> None:
    from document_enrichment.api.app import _freshness_differences
    from document_enrichment.models import CatalogSnapshot, Dataset, SchemaField

    urn = "urn:li:dataset:(urn:li:dataPlatform:test,orders,PROD)"
    baseline = [{"urn": urn, "description": "", "schema_fields": ["id"], "field_snapshots": [
        {"field_path": "id", "native_data_type": "BIGINT", "nullable": False, "description": "old"}
    ], "domain_urn": None, "owner_urns": [], "tag_urns": [], "deprecated": False, "referenced_fields": ["id"]}]
    snapshot = CatalogSnapshot(datasets=[Dataset(urn=urn, name="orders", qualified_name="orders", schema_fields=["id"], field_snapshots=[
        SchemaField(field_path="id", native_data_type="VARCHAR", nullable=True, description="new")
    ])])
    differences = _freshness_differences(baseline, snapshot)
    assert any("native_data_type: old='BIGINT' new='VARCHAR'" in item for item in differences)
    assert any("nullable: old=False new=True" in item for item in differences)


def test_review_accepts_twenty_dataset_associations(tmp_path, in_memory_catalog) -> None:
    with _client(tmp_path, in_memory_catalog) as client:
        uploaded = client.post(
            "/api/analyses", files={"file": ("x.md", b"# x\n```sql\nselect * from fct_orders\n```", "text/markdown")}
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
