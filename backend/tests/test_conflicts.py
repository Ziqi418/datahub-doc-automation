from __future__ import annotations

import json

import httpx
import pytest

from document_enrichment.config import Settings
from document_enrichment.datahub.conflicts import GraphQLDocumentConflictGateway


@pytest.mark.asyncio
async def test_document_search_uses_datahub_v16_schema(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "SearchDocumentsInput!" in payload["query"]
        assert "documents" in payload["query"]
        assert payload["variables"] == {"input": {"query": "revenue", "start": 0, "count": 20}}
        return httpx.Response(
            200,
            json={
                "data": {
                    "searchDocuments": {
                        "documents": [
                            {
                                "urn": "urn:li:document:revenue-definition",
                                "info": {
                                    "title": "Revenue definition",
                                    "contents": {"text": "Revenue equals settled payments."},
                                    "relatedAssets": [
                                        {
                                            "asset": {
                                                "urn": "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
                                            }
                                        }
                                    ],
                                },
                                "domain": {"domain": {"urn": "urn:li:domain:finance"}},
                            }
                        ]
                    }
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = GraphQLDocumentConflictGateway(
        Settings(database_path=tmp_path / "test.db"), client
    )

    documents = await gateway.search_documents(query="revenue")

    assert len(documents) == 1
    assert documents[0].urn == "urn:li:document:revenue-definition"
    assert documents[0].title == "Revenue definition"
    assert documents[0].text == "Revenue equals settled payments."
    assert documents[0].related_dataset_urns == [
        "urn:li:dataset:(urn:li:dataPlatform:jaffle_shop,fct_orders,PROD)"
    ]
    assert documents[0].domain_urn == "urn:li:domain:finance"
