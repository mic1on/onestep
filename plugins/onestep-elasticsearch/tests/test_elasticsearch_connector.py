from __future__ import annotations

import pytest

from onestep import Envelope
from onestep_elasticsearch.connector import ElasticsearchConnector


def test_mapping_becomes_one_newline_terminated_bulk_action() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", id_field="event_id"
    )
    chunks = sink._encode_chunks({"event_id": "evt-1", "value": 3})

    assert chunks == [
        b'{"index":{"_index":"events","_id":"evt-1"}}\n'
        b'{"event_id":"evt-1","value":3}\n'
    ]


def test_sequence_chunks_by_action_count() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", chunk_size=2
    )
    chunks = sink._encode_chunks([{"n": 1}, {"n": 2}, {"n": 3}])

    assert len(chunks) == 2
    assert chunks[0].count(b"\n") == 4
    assert chunks[1].count(b"\n") == 2


@pytest.mark.parametrize("body", [[], "text", ["text"], [{"ok": 1}, 2]])
def test_invalid_logical_batch_is_rejected(body) -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(index="events")

    with pytest.raises((TypeError, ValueError)):
        sink._encode_chunks(body)


def test_one_action_larger_than_byte_limit_is_rejected() -> None:
    sink = ElasticsearchConnector("http://search:9200").bulk_sink(
        index="events", max_chunk_bytes=40
    )

    with pytest.raises(ValueError, match="max_chunk_bytes"):
        sink._encode_chunks({"payload": "x" * 100})
