from onestep_control_plane_api.api.query_support import (
    extract_event_source_label,
    extract_source_label,
)


def test_extract_source_label_shortens_sqs_queue_url_to_queue_name() -> None:
    sqs_url = "https://sqs.cn-northwest-1.amazonaws.com.cn/928507961548/ceegic-bidding-signup.fifo"

    assert (
        extract_source_label(
            source_kind="sqs_queue",
            source_name=sqs_url,
            source_config={"url": sqs_url},
        )
        == "ceegic-bidding-signup.fifo"
    )


def test_extract_source_label_keeps_non_sqs_url_labels() -> None:
    url = "https://example.test/hooks/intake"

    assert (
        extract_source_label(
            source_kind="webhook",
            source_name=url,
            source_config={"url": url},
        )
        == url
    )


def test_extract_event_source_label_shortens_sqs_source_meta() -> None:
    sqs_url = "https://sqs.cn-northwest-1.amazonaws.com.cn/928507961548/ceegic-bidding-signup.fifo"

    assert extract_event_source_label(meta={"source": sqs_url}) == "ceegic-bidding-signup.fifo"
