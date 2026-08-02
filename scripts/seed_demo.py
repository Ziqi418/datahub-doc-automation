"""Idempotently seed the fixed Jaffle Shop namespace into a local DataHub instance.

This script only emits aspects for urn:li:dataPlatform:jaffle_shop and the fixed
domain/tag/team URNs below. It never deletes entities or scans existing assets.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METADATA_FILE = ROOT / "demo" / "metadata" / "jaffle_shop.json"


def load_metadata() -> dict:
    data = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    validate_metadata(data)
    return data


def validate_metadata(data: dict) -> None:
    required_counts = {"domains": 3, "tags": 7, "teams": 3, "datasets": 11}
    for key, expected in required_counts.items():
        if len(data.get(key, [])) != expected:
            raise ValueError(f"Expected {expected} {key}, got {len(data.get(key, []))}")
    domain_ids = {item["id"] for item in data["domains"]}
    tag_ids = {item["id"] for item in data["tags"]}
    team_ids = {item["id"] for item in data["teams"]}
    names = set()
    for dataset in data["datasets"]:
        if dataset["name"] in names:
            raise ValueError(f"Duplicate dataset name: {dataset['name']}")
        names.add(dataset["name"])
        if dataset["domain"] not in domain_ids or dataset["owner"] not in team_ids:
            raise ValueError(f"Unknown domain or owner for dataset {dataset['name']}")
        if not dataset["description"] or not dataset["fields"]:
            raise ValueError(f"Dataset {dataset['name']} needs description and schema fields")
        if not set(dataset["tags"]).issubset(tag_ids):
            raise ValueError(f"Unknown tag on dataset {dataset['name']}")


def seed(data: dict, gms_server: str, token: str | None) -> None:
    try:
        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.emitter.rest_emitter import DatahubRestEmitter
        from datahub.metadata.schema_classes import (
            CorpGroupInfoClass,
            CorpUserInfoClass,
            DatasetPropertiesClass,
            DomainPropertiesClass,
            DomainsClass,
            GlobalTagsClass,
            OtherSchemaClass,
            OwnerClass,
            OwnershipClass,
            OwnershipTypeClass,
            SchemaFieldClass,
            SchemaFieldDataTypeClass,
            SchemaMetadataClass,
            StringTypeClass,
            TagAssociationClass,
            TagPropertiesClass,
        )
    except ImportError as exc:
        raise RuntimeError("Install the datahub extra: cd backend && uv sync --extra datahub") from exc

    emitter = DatahubRestEmitter(gms_server=gms_server, token=token)

    def emit(urn: str, aspect: object) -> None:
        emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect))

    for domain in data["domains"]:
        emit(f"urn:li:domain:{domain['id']}", DomainPropertiesClass(name=domain["name"], description=domain["description"]))
    for tag in data["tags"]:
        emit(f"urn:li:tag:{tag['id']}", TagPropertiesClass(name=tag["id"], description=tag["description"]))
    for team in data["teams"]:
        emit(
            f"urn:li:corpGroup:{team['id']}",
            CorpGroupInfoClass(
                admins=[],
                members=[],
                groups=[],
                displayName=team["name"],
                description=team["description"],
            ),
        )
        # DataHub 1.6 permits CorpUser, but not CorpGroup, in Dataset ownership.
        # Keep the demo group and create a same-ID technical owner for each team.
        emit(
            f"urn:li:corpuser:{team['id']}",
            CorpUserInfoClass(active=True, displayName=team["name"], title="Demo team owner"),
        )
    platform_urn = f"urn:li:dataPlatform:{data['platform']}"
    for dataset in data["datasets"]:
        urn = f"urn:li:dataset:({platform_urn},{dataset['name']},{data['env']})"
        emit(urn, DatasetPropertiesClass(name=dataset["name"], description=dataset["description"], qualifiedName=dataset["name"]))
        emit(
            urn,
            SchemaMetadataClass(
                schemaName="jaffle_shop",
                platform=platform_urn,
                version=0,
                hash="jaffle-shop-mvp-v1",
                platformSchema=OtherSchemaClass(rawSchema="jaffle_shop demo schema"),
                fields=[
                    SchemaFieldClass(
                        fieldPath=field,
                        type=SchemaFieldDataTypeClass(type=StringTypeClass()),
                        nativeDataType="string",
                        description=f"{field} field",
                    )
                    for field in dataset["fields"]
                ],
            ),
        )
        emit(
            urn,
            OwnershipClass(
                owners=[
                    OwnerClass(
                        owner=f"urn:li:corpuser:{dataset['owner']}",
                        type=OwnershipTypeClass.TECHNICAL_OWNER,
                    )
                ]
            ),
        )
        emit(urn, DomainsClass(domains=[f"urn:li:domain:{dataset['domain']}"]))
        emit(urn, GlobalTagsClass(tags=[TagAssociationClass(tag=f"urn:li:tag:{tag}") for tag in dataset["tags"]]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Validate fixtures without connecting to DataHub")
    parser.add_argument("--gms-server", default=os.getenv("DATAHUB_GMS_URL", "http://localhost:8080"))
    parser.add_argument("--token", default=os.getenv("DATAHUB_TOKEN"))
    args = parser.parse_args()
    data = load_metadata()
    if args.dry_run:
        print("Demo metadata is valid: 11 datasets, 3 domains, 7 tags, 3 teams")
        return 0
    seed(data, args.gms_server, args.token)
    print("Seeded Jaffle Shop demo metadata idempotently.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
