"""Basic real-model quality evaluation for Gate 4 demo recommendations.

Requires LLM_API_KEY and LLM_MODEL in the repository-root .env file. It reads
only local demo fixtures and sends each document to the configured LLM.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from document_enrichment.config import Settings  # noqa: E402
from document_enrichment.recommendation.llm import OpenAICompatibleProvider, recommend_with_llm  # noqa: E402
from document_enrichment.recommendation.rules import recommend_rules  # noqa: E402
from test_llm_recommendations import build_catalog  # noqa: E402


def _entity_id(urn: str | None) -> str | None:
    if urn is None:
        return None
    if "," in urn:
        return urn.split(",", maxsplit=1)[1].rsplit(",", maxsplit=1)[0]
    return urn.rsplit(":", maxsplit=1)[-1]


def _ids(urns: list[str]) -> list[str]:
    return [_entity_id(urn) for urn in urns if _entity_id(urn) is not None]


def _metrics(expected_count: int, predicted_count: int, matched_count: int) -> dict[str, float | int]:
    return {
        "matched": matched_count,
        "expected": expected_count,
        "predicted": predicted_count,
        "precision": round(matched_count / predicted_count, 3) if predicted_count else 0.0,
        "recall": round(matched_count / expected_count, 3) if expected_count else 1.0,
    }


async def main() -> int:
    settings = Settings()
    if not settings.llm_api_key or not settings.llm_model:
        print("LLM_API_KEY and LLM_MODEL must be set in .env", file=sys.stderr)
        return 2

    metadata = json.loads((ROOT / "demo" / "metadata" / "jaffle_shop.json").read_text(encoding="utf-8"))
    catalog = build_catalog(metadata)
    provider = OpenAICompatibleProvider(settings)
    rows: list[dict[str, object]] = []
    try:
        for gold_path in sorted((ROOT / "demo" / "gold").glob("*.yaml")):
            gold = yaml.safe_load(gold_path.read_text(encoding="utf-8"))
            expected = gold["expected"]
            document_path = ROOT / "demo" / "documents" / gold["document"]
            text = document_path.read_text(encoding="utf-8")
            result = await recommend_with_llm(
                provider=provider,
                text=text,
                catalog=catalog,
                rule_recommendations=recommend_rules(text, document_path.name, catalog),
            )
            predicted = {
                "domain": _entity_id(result.domain.urn) if result.domain else None,
                "owner": _entity_id(result.owner.urn) if result.owner else None,
                "tags": _ids([item.urn for item in result.tags]),
                "datasets": _ids([item.urn for item in result.datasets]),
            }
            rows.append(
                {
                    "document": gold["document"],
                    "elapsed_ms": result.elapsed_ms,
                    "domain": {"expected": expected["domain"], "predicted": predicted["domain"]},
                    "owner": {"expected": expected["owner"], "predicted": predicted["owner"]},
                    "tags": _comparison(expected["tags"], predicted["tags"]),
                    "datasets": _comparison(expected["datasets"], predicted["datasets"]),
                }
            )
    finally:
        await provider.aclose()

    print(
        json.dumps(
            {
                "documents": rows,
                "summary": {
                    "documents_evaluated": len(rows),
                    "domain_accuracy": round(
                        sum(row["domain"]["expected"] == row["domain"]["predicted"] for row in rows) / len(rows), 3
                    ),
                    "owner_accuracy": round(
                        sum(row["owner"]["expected"] == row["owner"]["predicted"] for row in rows) / len(rows), 3
                    ),
                    "tag_micro": _aggregate_metrics(rows, "tags"),
                    "dataset_micro": _aggregate_metrics(rows, "datasets"),
                },
            },
            indent=2,
        )
    )
    return 0


def _comparison(expected: list[str], predicted: list[str]) -> dict[str, list[str]]:
    expected_set = set(expected)
    predicted_set = set(predicted)
    return {
        "expected": sorted(expected_set),
        "predicted": sorted(predicted_set),
        "missing": sorted(expected_set - predicted_set),
        "extra": sorted(predicted_set - expected_set),
    }


def _aggregate_metrics(rows: list[dict[str, object]], entity_type: str) -> dict[str, float | int]:
    expected_count = predicted_count = matched_count = 0
    for row in rows:
        comparison = row[entity_type]
        expected = set(comparison["expected"])
        predicted = set(comparison["predicted"])
        expected_count += len(expected)
        predicted_count += len(predicted)
        matched_count += len(expected & predicted)
    return _metrics(expected_count, predicted_count, matched_count)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
