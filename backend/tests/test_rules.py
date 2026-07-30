from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from document_enrichment.recommendation.rules import dataset_candidates, recommend_rules


def test_sql_reference_is_top_and_stable(catalog) -> None:
    text = "# Test\n```sql\nselect * from fct_orders join payments on 1=1\n```"
    first = dataset_candidates(text, "test.md", catalog)
    second = dataset_candidates(text, "test.md", catalog)
    assert [item.urn for item in first] == [item.urn for item in second]
    assert first[0].display_name == "fct_orders"
    assert first[0].confidence == 0.99
    assert first[0].evidence[0].kind == "sql_table_reference"


def test_ambiguous_short_name_keeps_all_candidates(catalog) -> None:
    duplicate = catalog.datasets[0].model_copy(update={"urn": "urn:li:dataset:(urn:li:dataPlatform:other,customers,PROD)", "qualified_name": "other.customers"})
    expanded = catalog.model_copy(update={"datasets": [*catalog.datasets, duplicate]})
    candidates = dataset_candidates("# Customer notes\ncustomers", "test.md", expanded)
    matching = [item for item in candidates if item.display_name.endswith("customers")]
    assert len(matching) == 2


def test_malformed_sql_falls_back_without_crashing(catalog) -> None:
    result = recommend_rules("# Bad SQL\n```sql\nselect * from payments where note = 'oops\n```", "bad.md", catalog)
    assert result.datasets[0].display_name == "payments"


@pytest.mark.parametrize("gold_path", sorted((Path(__file__).resolve().parents[2] / "demo" / "gold").glob("*.yaml")))
def test_demo_documents_have_rule_recall_at_30(catalog, gold_path: Path) -> None:
    gold = yaml.safe_load(gold_path.read_text())
    root = gold_path.parents[1]
    text = (root / "documents" / gold["document"]).read_text()
    result = dataset_candidates(text, gold["document"], catalog, limit=30)
    predicted = {item.display_name for item in result}
    assert set(gold["expected"]["datasets"]).issubset(predicted)
