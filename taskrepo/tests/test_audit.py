from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from trialops.audit import AuditChain, GENESIS_HASH


AT = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)


def append_event(chain, event_id="event-1", at=AT, payload=None):
    return chain.append(
        event_id=event_id,
        actor="actor-1",
        action="recorded",
        at=at,
        payload={"value": 1} if payload is None else payload,
    )


def rehash(record):
    unsigned = {key: value for key, value in record.items() if key != "hash"}
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    record["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return record


def test_genesis_utc_normalization_and_canonical_payload_order():
    chain = AuditChain()
    assert chain.head() == GENESIS_HASH
    offset_time = datetime(
        2024, 6, 15, 14, 30, tzinfo=timezone(timedelta(hours=2))
    )
    record = chain.append(
        event_id="event-1",
        actor="actor-1",
        action="recorded",
        at=offset_time,
        payload={"z": 1, "a": {"β": 2, "b": 3}},
    )

    assert record["sequence"] == 1
    assert record["at"] == "2024-06-15T12:30:00Z"
    assert record["previous_hash"] == GENESIS_HASH
    assert chain.previous_hash == record["hash"]
    assert chain.head() == record["hash"]
    unsigned = {key: value for key, value in record.items() if key != "hash"}
    expected = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert record["hash"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert chain.verify(chain.records) == []


def test_payload_insertion_order_does_not_change_hash():
    first = AuditChain()
    second = AuditChain()
    left = append_event(first, payload={"a": 1, "b": 2})
    right = append_event(second, payload={"b": 2, "a": 1})

    assert left["hash"] == right["hash"]


def test_tampering_and_sequence_gaps_are_detected():
    chain = AuditChain()
    append_event(chain)
    append_event(chain, "event-2", AT + timedelta(seconds=1))

    tampered = deepcopy(chain.records)
    tampered[0]["payload"]["value"] = 99
    assert "BAD_HASH" in chain.verify(tampered)

    gapped = deepcopy(chain.records)
    gapped[1]["sequence"] = 3
    errors = chain.verify(gapped)
    assert "BAD_SEQUENCE" in errors
    assert "BAD_HASH" in errors


def test_replay_is_not_a_valid_extension():
    chain = AuditChain()
    record = append_event(chain)

    errors = chain.verify([record, deepcopy(record)])

    assert errors == [
        "BAD_PREVIOUS_HASH",
        "BAD_SEQUENCE",
        "DUPLICATE_EVENT_ID",
        "NON_INCREASING_TIMESTAMP",
    ]


def test_append_validation_and_independent_chains():
    first = AuditChain()
    second = AuditChain()
    append_event(first)
    with pytest.raises(ValueError, match="unique"):
        append_event(first)
    assert len(first.records) == 1

    # Independent chains may reuse an event ID and have identical first hashes.
    assert append_event(second)["hash"] == first.records[0]["hash"]

    with pytest.raises(ValueError, match="timezone-aware"):
        append_event(AuditChain(), at=AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="microseconds"):
        append_event(AuditChain(), at=AT.replace(microsecond=1))
    with pytest.raises(ValueError, match="payload"):
        append_event(AuditChain(), payload=[])
    with pytest.raises(ValueError, match="actor"):
        first.append(
            event_id="event-2",
            actor=" ",
            action="recorded",
            at=AT,
            payload={},
        )


def test_trusted_head_and_length_detect_suffix_truncation():
    chain = AuditChain()
    append_event(chain)
    append_event(chain, "event-2", AT + timedelta(seconds=1))
    trusted_head = chain.head()

    assert chain.verify(
        chain.records, expected_head=trusted_head, expected_length=2
    ) == []
    assert chain.verify(chain.records[:1]) == []
    assert chain.verify(
        chain.records[:1], expected_head=trusted_head, expected_length=2
    ) == ["HEAD_MISMATCH", "LENGTH_MISMATCH"]


def test_independent_golden_record_hash():
    record = AuditChain().append(
        event_id="evt-golden",
        actor="actor",
        action="action",
        at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        payload={"b": 2, "a": "雪"},
    )

    assert record["hash"] == "92d5600f044f4bcd2134215e770f49cac60926e50670c3f58930eba13db528ba"
