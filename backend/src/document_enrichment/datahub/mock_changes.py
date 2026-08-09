"""Hackathon-only stand-in for a database migration/change-feed service."""

from __future__ import annotations

from document_enrichment.models import DatabaseChange


def recent_database_changes() -> list[DatabaseChange]:
    # Replace this adapter with a migration-service or CDC client in production.
    return [
        DatabaseChange(
            id="mock-2026-08-08-fct-orders-gross-revenue",
            dataset_urn="urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)",
            kind="field_renamed",
            field_path="gross_revenue",
            replacement_field_path="gross_amount",
            migration_id="20260808_001",
            summary="fct_orders.gross_revenue was renamed to gross_amount",
        )
    ]
