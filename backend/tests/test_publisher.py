from __future__ import annotations

from datahub.metadata.schema_classes import DocumentStateClass
from datahub.sdk import Document

from document_enrichment.models import ReviewSelection
from document_enrichment.publishing.publisher import _verify


def test_verify_accepts_sdk_domain_urn_object() -> None:
    selection = ReviewSelection(
        domain_urns=["urn:li:domain:finance"],
        tag_urns=["urn:li:tag:finance"],
        owner_urns=["urn:li:corpuser:finance-analytics"],
        dataset_urns=["urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"],
    )
    document = Document.create_document(
        id="publish-verification",
        title="Revenue policy",
        text="# Revenue policy",
        status=DocumentStateClass.UNPUBLISHED,
        domain=selection.domain_urns[0],
        tags=selection.tag_urns,
        owners=selection.owner_urns,
        related_assets=selection.dataset_urns,
    )

    _verify(document, "Revenue policy", "# Revenue policy", selection)
