from document_enrichment.models import CatalogSnapshot, Dataset
from document_enrichment.recommendation.rules import (
    dataset_candidates,
    merge_dataset_candidates,
    recommend_rules,
)


def _dataset(urn_suffix: str, name: str, qualified_name: str | None = None) -> Dataset:
    return Dataset(
        urn=f"urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,{urn_suffix},PROD)",
        name=name,
        qualified_name=qualified_name or name,
    )


def test_sql_reference_is_ranked_with_evidence() -> None:
    catalog = CatalogSnapshot(datasets=[_dataset(name, name) for name in ["orders", "fct_orders"]])
    candidates = dataset_candidates("```sql\nSELECT * FROM fct_orders\n```", "report.md", catalog)
    assert [candidate.urn for candidate in candidates] == [_dataset("fct_orders", "fct_orders").urn]
    assert candidates[0].evidence[0].kind == "sql_table_reference"


def test_short_name_collision_is_retained() -> None:
    catalog = CatalogSnapshot(
        datasets=[
            _dataset("raw_orders", "orders", "raw.orders"),
            _dataset("warehouse_orders", "orders", "warehouse.orders"),
        ]
    )
    candidates = dataset_candidates("The orders dataset is reviewed daily.", "report.md", catalog)
    assert len(candidates) == 2
    assert [candidate.urn for candidate in candidates] == sorted(
        candidate.urn for candidate in candidates
    )


def test_malformed_sql_falls_back_without_crashing(catalog) -> None:
    result = recommend_rules(
        "# Bad SQL\n```sql\nselect * from payments where note = 'oops\n```", "bad.md", catalog
    )
    assert result.datasets[0].display_name == "payments"


def test_keyword_merge_cannot_displace_sql_anchor() -> None:
    sql = _dataset("fct_orders", "fct_orders")
    keyword = _dataset("customers", "customers")
    deterministic = dataset_candidates(
        "```sql\nselect * from fct_orders\n```", "report.md", CatalogSnapshot(datasets=[sql])
    )
    merged = merge_dataset_candidates(deterministic, [keyword], limit=30)
    assert [item.urn for item in merged] == [sql.urn, keyword.urn]
    assert any(item.kind == "sql_table_reference" for item in merged[0].evidence)
    assert merged[1].source == "datahub_keyword"
