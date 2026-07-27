from __future__ import annotations

import os
import uuid

import httpx
import pytest
from onestep_elasticsearch import ElasticsearchConnector

from onestep import Envelope

URL = os.getenv("ONESTEP_ELASTICSEARCH_URL") or os.getenv("ONESTEP_OPENSEARCH_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="search URL is not configured"),
]


@pytest.mark.asyncio
async def test_live_bulk_write_and_deterministic_replay() -> None:
    index = f"onestep-{uuid.uuid4().hex}"
    connector = ElasticsearchConnector(URL, distribution="auto")
    sink = connector.bulk_sink(index=index, id_field="id", refresh=True, chunk_size=1)
    try:
        await sink.send(
            Envelope(body=[{"id": "one", "value": 1}, {"id": "two", "value": 2}])
        )
        await sink.send(Envelope(body={"id": "one", "value": 3}))
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{URL.rstrip('/')}/{index}/_doc/one")
        assert response.status_code == 200
        assert response.json()["_source"]["value"] == 3
    finally:
        async with httpx.AsyncClient() as client:
            await client.delete(f"{URL.rstrip('/')}/{index}")
        await connector.close()
