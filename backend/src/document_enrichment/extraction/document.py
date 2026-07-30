from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FENCE_RE = re.compile(r"```(?:sql|postgres|postgresql)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_TABLE_RE = re.compile(
    r"\b(?:from|join|into|update|merge\s+into)\s+([`\"\[]?[a-zA-Z_][\w.$-]*[`\"\]]?)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-zA-Z_][\w.-]*")


@dataclass(frozen=True)
class Match:
    text: str
    line_start: int
    line_end: int

    @property
    def location(self) -> str:
        return f"lines {self.line_start}-{self.line_end}"


@dataclass(frozen=True)
class ExtractedDocument:
    title: str
    body: str
    sql_blocks: tuple[Match, ...]
    table_references: tuple[Match, ...]
    tokens: frozenset[str]
    parser_fallback: bool = False


def extract_document(text: str, source_filename: str = "document.md") -> ExtractedDocument:
    """Extract non-executable signals from Markdown/TXT. SQL is never executed."""
    title = _title(text, source_filename)
    sql_blocks = tuple(_matches(_FENCE_RE, text))
    table_references: list[Match] = []
    fallback = False
    for block in sql_blocks:
        try:
            table_references.extend(_sql_tables(block))
        except ValueError:
            # We still provide lexical table candidates when SQL is malformed.
            fallback = True
            table_references.extend(_regex_tables(block))
    tokens = frozenset(token.casefold() for token in _TOKEN_RE.findall(text))
    return ExtractedDocument(
        title=title,
        body=text,
        sql_blocks=sql_blocks,
        table_references=tuple(table_references),
        tokens=tokens,
        parser_fallback=fallback,
    )


def _title(text: str, source_filename: str) -> str:
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("#"):
            title = candidate.lstrip("#").strip()
            if title:
                return title[:200]
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return Path(source_filename).stem.replace("-", " ").replace("_", " ")[:200]


def _matches(pattern: re.Pattern[str], text: str) -> list[Match]:
    result: list[Match] = []
    for match in pattern.finditer(text):
        start = text.count("\n", 0, match.start()) + 1
        end = text.count("\n", 0, match.end()) + 1
        result.append(Match(text=match.group(1), line_start=start, line_end=end))
    return result


def _sql_tables(block: Match) -> list[Match]:
    # Reject obviously unbalanced quoting to exercise a safe deterministic fallback.
    if block.text.count("'") % 2 or block.text.count('"') % 2:
        raise ValueError("unbalanced SQL quote")
    return _regex_tables(block)


def _regex_tables(block: Match) -> list[Match]:
    refs: list[Match] = []
    for match in _TABLE_RE.finditer(block.text):
        line_start = block.line_start + block.text.count("\n", 0, match.start())
        line_end = block.line_start + block.text.count("\n", 0, match.end())
        name = match.group(1).strip("`\"[]")
        refs.append(Match(text=name, line_start=line_start, line_end=line_end))
    return refs
