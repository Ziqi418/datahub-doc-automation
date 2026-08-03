from document_enrichment.extraction.document import extract_document


def test_extract_document_uses_sql_parser_for_quoted_qualified_tables() -> None:
    text = """# Monthly finance report

```sql
SELECT *
FROM analytics.fct_orders o
JOIN \"warehouse\".refunds r ON r.order_id = o.order_id
```
"""

    extracted = extract_document(text)

    assert extracted.title == "Monthly finance report"
    assert [(item.text, item.location) for item in extracted.table_references] == [
        ("analytics.fct_orders", "lines 5-5"),
        ("warehouse.refunds", "lines 6-6"),
    ]
    assert not extracted.parser_fallback


def test_extract_document_falls_back_when_sql_cannot_be_parsed() -> None:
    text = """# Broken query
```sql
SELECT * FROM fct_orders WHERE note = 'unterminated
```
"""

    extracted = extract_document(text)

    assert extracted.parser_fallback
    assert [(item.text, item.location) for item in extracted.table_references] == [
        ("fct_orders", "lines 3-3"),
    ]


def test_extract_document_uses_first_heading_or_first_non_empty_line() -> None:
    assert extract_document("\n# A heading\nbody").title == "A heading"
    assert extract_document("\nPlain title\nbody", "ignored.md").title == "Plain title"
    assert extract_document("\n\n", "monthly-report.txt").title == "monthly report"


def test_extract_document_keeps_qualified_identifier_parts_as_tokens() -> None:
    extracted = extract_document("Use customers.customer_id and products.product_id.")

    assert {"customers", "customer_id", "products", "product_id"}.issubset(extracted.tokens)
