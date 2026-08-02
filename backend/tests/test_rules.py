from document_enrichment.models import CatalogSnapshot, Dataset
from document_enrichment.recommendation.rules import dataset_candidates


def _dataset(urn_suffix: str, name: str, qualified_name: str | None = None) -> Dataset:
    return Dataset(
        urn=f"urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,{urn_suffix},PROD)",
        name=name,
        qualified_name=qualified_name or name,
    )


def test_explicit_sql_table_reference_is_in_top_five_with_evidence() -> None:
    catalog = CatalogSnapshot(
        datasets=[_dataset(name, name) for name in ["customers", "orders", "payments", "refunds", "fct_orders", "stores"]]
    )

    candidates = dataset_candidates(
        "```sql\nSELECT * FROM fct_orders\n```",
        "report.md",
        catalog,
    )

    assert [candidate.urn for candidate in candidates[:5]] == [
        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
    ]
    assert candidates[0].evidence[0].kind == "sql_table_reference"
    assert candidates[0].evidence[0].location == "lines 2-2"


def test_short_name_collision_keeps_all_candidates_and_evidence() -> None:
    catalog = CatalogSnapshot(
        datasets=[
            _dataset("raw_orders", "orders", "raw.orders"),
            _dataset("warehouse_orders", "orders", "warehouse.orders"),
        ]
    )

    candidates = dataset_candidates("The orders dataset is reviewed daily.", "report.md", catalog)

    assert [candidate.urn for candidate in candidates] == [
        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,raw_orders,PROD)",
        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,warehouse_orders,PROD)",
    ]
    assert all(candidate.evidence[0].matched_text == "orders" for candidate in candidates)


def test_repeated_candidate_retrieval_has_stable_order() -> None:
    catalog = CatalogSnapshot(datasets=[_dataset("b", "b"), _dataset("a", "a")])

    first = dataset_candidates("a b", "report.md", catalog)
    second = dataset_candidates("a b", "report.md", catalog)

    assert first == second
    assert [candidate.urn for candidate in first] == [
        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,a,PROD)",
        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,b,PROD)",
    ]
