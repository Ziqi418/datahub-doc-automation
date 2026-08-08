"""Read-only field-reference extraction and schema resolution for publication."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from sqlglot import exp, parse
from sqlglot.errors import ParseError, TokenError

from document_enrichment.models import Dataset, FieldReference

_INLINE_CODE = re.compile(r"`([A-Za-z_][\w.-]*(?:\.[A-Za-z_][\w.-]*)+)`")


def lint_schema_references(content: str, datasets: list[Dataset]) -> list[FieldReference]:
    """Resolve explicit SQL references against *only* the selected current schemas."""
    by_table = _dataset_names(datasets)
    results: list[FieldReference] = []
    sql_spans: list[tuple[int, int]] = []
    for block in re.finditer(r"```(?:sql|postgres|postgresql)?\s*\n(.*?)```", content, re.I | re.S):
        sql_spans.append((block.start(1), block.end(1)))
        line_offset = content.count("\n", 0, block.start(1))
        try:
            expressions = parse(block.group(1), error_level="RAISE")
        except (ParseError, TokenError, ValueError):
            continue
        for expression in expressions:
            aliases = _aliases(expression, by_table)
            for column in expression.find_all(exp.Column):
                column_name = column.name
                if not column_name or column_name == "*":
                    continue
                table = column.table
                candidates = aliases.get(table.casefold(), []) if table else list(datasets)
                results.append(_resolve(column_name, table or None, candidates, line_offset + int(column.meta.get("line") or 1), "sql", "high" if table else "medium"))
    for match in _INLINE_CODE.finditer(content):
        if any(start <= match.start(1) < end for start, end in sql_spans):
            continue
        parts = match.group(1).split(".")
        field, table = parts[-1], parts[-2] if len(parts) > 1 else None
        candidates = by_table.get(table.casefold(), []) if table else []
        results.append(_resolve(field, table, candidates, content.count("\n", 0, match.start(1)) + 1, "markdown_inline_code", "low"))
    # Identical references from repeated parser traversal are noise, not independent risks.
    return list({reference.id: reference for reference in results}.values())


def _dataset_names(datasets: list[Dataset]) -> dict[str, list[Dataset]]:
    names: dict[str, list[Dataset]] = defaultdict(list)
    for item in datasets:
        # A short qualified name commonly equals ``name``.  Store each Dataset
        # once per lookup key so it cannot become ambiguous by itself.
        for value in {item.name, item.qualified_name, item.qualified_name.split(".")[-1]}:
            if item.urn not in {candidate.urn for candidate in names[value.casefold()]}:
                names[value.casefold()].append(item)
    return names


def _aliases(expression: exp.Expression, by_table: dict[str, list[Dataset]]) -> dict[str, list[Dataset]]:
    aliases: dict[str, list[Dataset]] = {}
    for table in expression.find_all(exp.Table):
        name = ".".join(part for part in (table.catalog, table.db, table.name) if part)
        candidates = by_table.get(name.casefold(), by_table.get(table.name.casefold(), []))
        if table.alias:
            aliases[table.alias.casefold()] = candidates
        aliases[table.name.casefold()] = candidates
    return aliases


def _resolve(field: str, table: str | None, candidates: list[Dataset], line: int, source: str, confidence: str) -> FieldReference:
    candidates = list({item.urn: item for item in candidates}.values())
    matches = [item for item in candidates if field.casefold() in {x.casefold() for x in item.schema_fields}]
    state = "resolved" if len(matches) == 1 else "ambiguous" if len(matches) > 1 else "unresolved"
    reason = ("field exists in selected dataset" if state == "resolved" else
              "field exists in multiple selected datasets" if state == "ambiguous" else
              "field is not present in the current selected dataset schema")
    raw = f"{table + '.' if table else ''}{field}"
    identifier = hashlib.sha256(f"{source}:{line}:{raw}".encode()).hexdigest()[:16]
    return FieldReference(id=identifier, raw_reference=raw, field_path=field, table_or_alias=table,
        location=f"line {line}", source=source, confidence=confidence, status=state,
        candidate_dataset_urns=[item.urn for item in (matches or candidates)], reason=reason,
        high_risk=state == "unresolved" and confidence == "high")
