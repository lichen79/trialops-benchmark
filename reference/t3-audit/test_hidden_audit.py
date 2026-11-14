from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from trialops.audit import AuditChain, GENESIS_HASH


AT = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def append(chain, event_id="event-1", *, at=AT, payload=None):
    return chain.append(
        event_id=event_id,
        actor="actor",
        action="action",
        at=at,
        payload={"nested": {"b": 2, "a": 1}} if payload is None else payload,
    )


def rehash(record):
    unsigned = {key: value for key, value in record.items() if key != "hash"}
    serialized = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    record["hash"] = hashlib.sha256(serialized).hexdigest()
    return record


def single_record():
    return append(AuditChain())


def test_empty_and_iterator_records_verify():
    chain = AuditChain()
    append(chain)
    assert chain.verify([]) == []
    assert AuditChain.verify(iter(chain.records)) == []


def test_hash_is_over_every_non_hash_field_in_normal_sorted_serialization():
    record = single_record()
    assert set(record) == {
        "sequence",
        "event_id",
        "actor",
        "action",
        "at",
        "payload",
        "previous_hash",
        "hash",
    }
    without_hash = {key: value for key, value in record.items() if key != "hash"}
    serialized = json.dumps(
        without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert record["hash"] == hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def test_negative_utc_offset_normalizes_across_a_date_boundary():
    zone = timezone(timedelta(hours=-5, minutes=-30))
    record = append(AuditChain(), at=datetime(2024, 1, 1, 22, 0, 0, tzinfo=zone))
    assert record["at"] == "2024-01-02T03:30:00Z"


@pytest.mark.parametrize(
    "timestamp",
    [
        "2024-01-02T03:04:05+00:00",
        "2024-01-02T03:04:05.000000Z",
        "2024-02-30T03:04:05Z",
        "2024-01-02 03:04:05Z",
        "2024-1-02T03:04:05Z",
        None,
    ],
)
def test_malformed_record_timestamps_are_reported(timestamp):
    record = single_record()
    record["at"] = timestamp
    rehash(record)
    assert AuditChain.verify([record]) == ["MALFORMED_TIMESTAMP"]


@pytest.mark.parametrize("bad_hash", ["abc", "g" * 64, "A" * 64, None])
def test_malformed_record_hashes_are_reported(bad_hash):
    record = single_record()
    record["hash"] = bad_hash
    assert AuditChain.verify([record]) == ["MALFORMED_HASH"]


def test_malformed_previous_hash_and_bad_linkage_are_distinct():
    record = single_record()
    record["previous_hash"] = "bad"
    rehash(record)
    assert AuditChain.verify([record]) == ["MALFORMED_HASH"]

    record = single_record()
    record["previous_hash"] = "1" * 64
    rehash(record)
    assert AuditChain.verify([record]) == ["BAD_PREVIOUS_HASH"]


def test_valid_but_wrong_hash_is_bad_hash_not_malformed():
    record = single_record()
    record["hash"] = "1" * 64
    assert AuditChain.verify([record]) == ["BAD_HASH"]


def test_non_dict_payload_is_detected_even_with_a_valid_record_hash():
    record = single_record()
    record["payload"] = []
    rehash(record)
    assert AuditChain.verify([record]) == ["BAD_PAYLOAD"]


def test_boolean_sequence_is_not_an_integer_sequence():
    record = single_record()
    record["sequence"] = True
    rehash(record)
    assert AuditChain.verify([record]) == ["BAD_SEQUENCE"]


def test_equal_and_decreasing_append_timestamps_fail_verification():
    for second_time in (AT, AT - timedelta(seconds=1)):
        chain = AuditChain()
        append(chain)
        append(chain, "event-2", at=second_time)
        assert chain.verify(chain.records) == ["NON_INCREASING_TIMESTAMP"]


def test_duplicate_event_id_can_be_reported_without_other_errors():
    chain = AuditChain()
    first = append(chain)
    second = append(chain, "event-2", at=AT + timedelta(seconds=1))
    second["event_id"] = first["event_id"]
    rehash(second)
    assert AuditChain.verify(chain.records) == ["DUPLICATE_EVENT_ID"]


def test_failed_append_does_not_consume_sequence_or_event_id():
    chain = AuditChain()
    with pytest.raises(ValueError, match="JSON"):
        append(chain, payload={"not_json": object()})
    record = append(chain, payload={})
    assert record["sequence"] == 1
    assert record["previous_hash"] == GENESIS_HASH
    assert chain.verify(chain.records) == []


def test_input_payload_is_copied_before_being_stored():
    chain = AuditChain()
    payload = {"nested": {"values": [1, 2]}}
    record = append(chain, payload=payload)
    payload["nested"]["values"].append(3)
    assert record["payload"] == {"nested": {"values": [1, 2]}}
    assert chain.verify(chain.records) == []


def test_unicode_payload_order_is_canonical_across_independent_chains():
    first = append(AuditChain(), payload={"é": "雪", "a": [2, {"β": 1}]})
    second = append(AuditChain(), payload={"a": [2, {"β": 1}], "é": "雪"})
    assert first["hash"] == second["hash"]


def test_multiple_validation_errors_have_sorted_unique_deterministic_order():
    record = single_record()
    record.update(
        sequence=2,
        at="bad",
        payload=[],
        previous_hash="bad",
        hash="bad",
    )
    expected = [
        "BAD_PAYLOAD",
        "BAD_SEQUENCE",
        "MALFORMED_HASH",
        "MALFORMED_TIMESTAMP",
    ]
    assert AuditChain.verify([record]) == expected
    assert AuditChain.verify([record]) == expected


def test_malformed_records_return_codes_instead_of_leaking_exceptions():
    assert AuditChain.verify(None) == ["MALFORMED_RECORD"]
    assert AuditChain.verify([None]) == ["MALFORMED_RECORD"]
    record = single_record()
    del record["actor"]
    errors = AuditChain.verify([record])
    assert "MALFORMED_RECORD" in errors


@pytest.mark.parametrize("field", ["event_id", "actor", "action"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_append_rejects_empty_required_strings(field, value):
    arguments = {
        "event_id": "event-1",
        "actor": "actor",
        "action": "action",
        "at": AT,
        "payload": {},
    }
    arguments[field] = value
    with pytest.raises(ValueError, match=field):
        AuditChain().append(**arguments)


def test_naive_non_datetime_and_fractional_timestamps_are_rejected():
    for value in (AT.replace(tzinfo=None), AT.isoformat(), AT.replace(microsecond=1)):
        with pytest.raises(ValueError, match="at|microseconds"):
            append(AuditChain(), at=value)


def test_empty_chain_head_and_expected_empty_verification():
    chain = AuditChain()
    assert chain.head() == GENESIS_HASH
    assert chain.verify(
        [], expected_head=GENESIS_HASH, expected_length=0
    ) == []
    assert chain.verify([], expected_head="1" * 64) == ["HEAD_MISMATCH"]
    assert chain.verify([], expected_length=1) == ["LENGTH_MISMATCH"]


def test_expected_head_and_length_are_independent_checks():
    record = single_record()
    assert AuditChain.verify([record], expected_length=2) == ["LENGTH_MISMATCH"]
    assert AuditChain.verify([record], expected_head="1" * 64) == ["HEAD_MISMATCH"]
    assert AuditChain.verify(
        [record], expected_head=record["hash"], expected_length=1
    ) == []


def test_trusted_head_detects_a_fully_rehashed_edit():
    chain = AuditChain()
    append(chain)
    append(chain, "event-2", at=AT + timedelta(seconds=1))
    trusted_head = chain.head()
    edited = deepcopy(chain.records)
    edited[0]["payload"]["nested"]["a"] = 99
    rehash(edited[0])
    edited[1]["previous_hash"] = edited[0]["hash"]
    rehash(edited[1])

    assert AuditChain.verify(edited) == []
    assert AuditChain.verify(
        edited, expected_head=trusted_head, expected_length=2
    ) == ["HEAD_MISMATCH"]


def test_trusted_head_and_length_detect_every_truncated_suffix():
    chain = AuditChain()
    for index in range(3):
        append(chain, f"event-{index}", at=AT + timedelta(seconds=index))

    for length in (0, 1, 2):
        truncated = chain.records[:length]
        assert AuditChain.verify(truncated) == []
        assert AuditChain.verify(
            truncated, expected_head=chain.head(), expected_length=3
        ) == ["HEAD_MISMATCH", "LENGTH_MISMATCH"]


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_append_rejects_nonfinite_json_numbers_without_advancing_head(number):
    chain = AuditChain()
    with pytest.raises(ValueError, match="JSON"):
        append(chain, payload={"number": number})
    assert chain.records == []
    assert chain.head() == GENESIS_HASH


def test_verify_rejects_nonfinite_payload_numbers_without_leaking_json_errors():
    record = single_record()
    record["payload"] = {"number": float("nan")}
    assert AuditChain.verify([record]) == ["BAD_PAYLOAD"]


def test_verify_does_not_normalize_an_offset_timestamp_to_utc():
    record = single_record()
    record["at"] = "2024-01-02T04:04:05+01:00"
    rehash(record)
    assert AuditChain.verify([record]) == ["MALFORMED_TIMESTAMP"]


def populated_chain(count=3):
    chain = AuditChain()
    for index in range(count):
        append(
            chain,
            f"event-{index}",
            at=AT + timedelta(seconds=index),
            payload={"nested": {"values": [index]}},
        )
    return chain


def test_from_records_restores_linkage_and_sequence_from_a_one_shot_snapshot():
    source = populated_chain()
    snapshot = deepcopy(source.records)

    class OneShotRecords:
        def __init__(self, records):
            self.records = records
            self.consumed = False

        def __iter__(self):
            if self.consumed:
                raise AssertionError("snapshot was materialized more than once")
            self.consumed = True
            return iter(self.records)

    restored = AuditChain.from_records(
        OneShotRecords(snapshot),
        expected_head=source.head(),
        expected_length=3,
    )
    assert restored.records == snapshot
    assert restored.head() == source.head()

    record = append(restored, "event-3", at=AT + timedelta(seconds=3))
    assert record["sequence"] == 4
    assert record["previous_hash"] == source.head()
    assert restored.head() == record["hash"]
    assert AuditChain.verify(
        restored.records, expected_head=record["hash"], expected_length=4
    ) == []
    assert source.records == snapshot


def test_restored_duplicate_id_rejection_does_not_advance_state():
    source = populated_chain(2)
    restored = AuditChain.from_records(
        source.records, expected_head=source.head(), expected_length=2
    )
    records_before = deepcopy(restored.records)
    head_before = restored.head()

    with pytest.raises(ValueError, match="event_id must be unique"):
        append(restored, "event-0", at=AT + timedelta(seconds=2))

    assert restored.records == records_before
    assert restored.head() == head_before
    record = append(restored, "event-2", at=AT + timedelta(seconds=2))
    assert record["sequence"] == 3
    assert record["previous_hash"] == head_before


def test_from_records_deep_copies_records_in_both_directions():
    source = populated_chain(2)
    snapshot = deepcopy(source.records)
    restored = AuditChain.from_records(
        snapshot, expected_head=source.head(), expected_length=2
    )

    assert restored.records is not snapshot
    assert all(
        restored_record is not snapshot_record
        for restored_record, snapshot_record in zip(restored.records, snapshot)
    )

    snapshot[0]["actor"] = "changed"
    snapshot[0]["payload"]["nested"]["values"].append(99)
    assert restored.records == source.records
    assert AuditChain.verify(
        restored.records, expected_head=source.head(), expected_length=2
    ) == []

    restored.records[1]["payload"]["nested"]["values"].append(7)
    assert snapshot[1] == source.records[1]


def test_from_records_rejects_an_unrehashed_edit():
    source = populated_chain(2)
    edited = deepcopy(source.records)
    edited[0]["payload"]["nested"]["values"].append(99)
    assert AuditChain.verify(edited) == ["BAD_HASH"]

    with pytest.raises(ValueError) as error:
        AuditChain.from_records(
            edited, expected_head=source.head(), expected_length=2
        )
    assert str(error.value) == "INVALID_CHAIN:BAD_HASH"


def test_from_records_rejects_a_valid_chain_with_the_wrong_trusted_anchor():
    source = populated_chain(2)
    with pytest.raises(ValueError) as error:
        AuditChain.from_records(
            source.records, expected_head="1" * 64, expected_length=2
        )
    assert str(error.value) == "INVALID_CHAIN:HEAD_MISMATCH"


def test_from_records_rejects_a_fully_rehashed_edit_at_the_trusted_head():
    source = populated_chain()
    edited = deepcopy(source.records)
    edited[0]["payload"]["nested"]["values"].append(99)
    for index, record in enumerate(edited):
        if index:
            record["previous_hash"] = edited[index - 1]["hash"]
        rehash(record)

    assert AuditChain.verify(edited) == []
    assert edited[-1]["hash"] != source.head()
    with pytest.raises(ValueError) as error:
        AuditChain.from_records(
            edited, expected_head=source.head(), expected_length=3
        )
    assert str(error.value) == "INVALID_CHAIN:HEAD_MISMATCH"


def test_from_records_truncation_errors_are_exact_and_deterministic():
    source = populated_chain()
    expected = "INVALID_CHAIN:HEAD_MISMATCH,LENGTH_MISMATCH"

    for length in (0, 1, 2):
        messages = []
        for _ in range(2):
            with pytest.raises(ValueError) as error:
                AuditChain.from_records(
                    source.records[:length],
                    expected_head=source.head(),
                    expected_length=3,
                )
            messages.append(str(error.value))
        assert messages == [expected, expected]


def test_from_records_empty_genesis_snapshot_restores_append_behavior():
    restored = AuditChain.from_records(
        iter(()), expected_head=GENESIS_HASH, expected_length=0
    )
    assert restored.records == []
    assert restored.head() == GENESIS_HASH

    record = append(restored)
    assert record["sequence"] == 1
    assert record["previous_hash"] == GENESIS_HASH
    assert restored.head() == record["hash"]


@pytest.mark.parametrize(
    ("expected_head", "expected_length", "message"),
    [
        ("1" * 64, 0, "INVALID_CHAIN:HEAD_MISMATCH"),
        (GENESIS_HASH, 1, "INVALID_CHAIN:LENGTH_MISMATCH"),
        ("1" * 64, 1, "INVALID_CHAIN:HEAD_MISMATCH,LENGTH_MISMATCH"),
    ],
)
def test_from_records_empty_snapshot_requires_genesis_head_and_zero_length(
    expected_head, expected_length, message
):
    with pytest.raises(ValueError) as error:
        AuditChain.from_records(
            [], expected_head=expected_head, expected_length=expected_length
        )
    assert str(error.value) == message


def test_independent_restored_instances_produce_identical_subsequent_records():
    source = populated_chain(2)
    first = AuditChain.from_records(
        source.records, expected_head=source.head(), expected_length=2
    )
    second = AuditChain.from_records(
        deepcopy(source.records), expected_head=source.head(), expected_length=2
    )

    first_record = append(
        first,
        "event-2",
        at=AT + timedelta(seconds=2),
        payload={"é": "雪", "a": [2, {"β": 1}]},
    )
    second_record = append(
        second,
        "event-2",
        at=AT + timedelta(seconds=2),
        payload={"a": [2, {"β": 1}], "é": "雪"},
    )

    assert first_record == second_record
    assert first_record is not second_record
    assert first_record["payload"] is not second_record["payload"]
    assert first.head() == second.head()
    assert first.records == second.records
    assert len(source.records) == 2
    assert source.head() == source.records[-1]["hash"]


def test_from_records_requires_both_trusted_arguments():
    with pytest.raises(TypeError):
        AuditChain.from_records([], expected_head=GENESIS_HASH)
    with pytest.raises(TypeError):
        AuditChain.from_records([], expected_length=0)


def test_from_records_materialization_failure_is_an_invalid_chain():
    with pytest.raises(ValueError) as error:
        AuditChain.from_records(
            None, expected_head=GENESIS_HASH, expected_length=0
        )
    assert str(error.value) == "INVALID_CHAIN:MALFORMED_RECORD"
