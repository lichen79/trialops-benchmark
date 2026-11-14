from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from trialops.safety import triage_events


UTC_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def event(event_id, **changes):
    value = {
        "event_id": event_id,
        "subject_pseudonym": "pseudo-1",
        "event_code": "CODE-1",
        "onset": "2024-01-01T00:00:00Z",
        "seriousness": "other",
        "source": "source-z",
    }
    value.update(changes)
    return value


@pytest.mark.parametrize(
    ("seriousness", "expected"),
    [
        ("fatal", "2024-01-08T00:00:00Z"),
        ("life_threatening", "2024-01-08T00:00:00Z"),
        ("hospitalization", "2024-01-16T00:00:00Z"),
        ("other", "2024-01-31T00:00:00Z"),
    ],
)
def test_reporting_windows_and_deadline_boundary(seriousness, expected):
    [case] = triage_events(
        [event("root", seriousness=seriousness)],
        now=datetime.fromisoformat(expected.replace("Z", "+00:00")),
    )
    assert case["report_by"] == expected
    assert case["overdue"] is False


def test_earliest_onset_and_highest_seriousness_come_from_the_whole_chain():
    events = [
        event(
            "followup",
            follow_up_of="root",
            onset="2024-01-01T00:00:00Z",
            source="source-a",
            uncertain=True,
        ),
        event(
            "root",
            onset="2024-01-10T00:00:00Z",
            seriousness="fatal",
            source="source-z",
        ),
        event(
            "last",
            follow_up_of="followup",
            onset="2024-01-05T00:00:00Z",
            seriousness="hospitalization",
            source="source-a",
        ),
    ]
    snapshot = deepcopy(events)

    [case] = triage_events(events, now=UTC_NOW)
    assert case == {
        "case_id": "root",
        "subject_pseudonym": "pseudo-1",
        "event_code": "CODE-1",
        "onset": "2024-01-01T00:00:00Z",
        "seriousness": "fatal",
        "report_by": "2024-01-08T00:00:00Z",
        "sources": ["source-a", "source-z"],
        "event_ids": ["followup", "last", "root"],
        "uncertain": True,
        "overdue": True,
    }
    assert events == snapshot


def test_uncertainty_defaults_false_and_sources_are_unique():
    [case] = triage_events(
        [
            event("A", source="same"),
            event("B", follow_up_of="A", source="same", uncertain=False),
            event("C", follow_up_of="B", source="same"),
        ],
        now=UTC_NOW,
    )
    assert case["sources"] == ["same"]
    assert case["uncertain"] is False


def test_case_sorting_uses_instants_then_root_id():
    events = [
        event("B", onset="2023-12-31T19:00:00-05:00"),
        event("A", onset="2024-01-01T01:00:00+01:00"),
        event("C", onset="2023-12-31T23:59:59Z"),
    ]
    cases = triage_events(events, now=UTC_NOW)
    assert [case["case_id"] for case in cases] == ["C", "A", "B"]


def test_fractional_seconds_survive_utc_normalization_and_deadline_math():
    onset = "2024-05-01T00:00:00.123456+05:30"
    deadline = datetime(2024, 5, 30, 18, 30, 0, 123456, tzinfo=timezone.utc)
    [case] = triage_events([event("A", onset=onset)], now=deadline)
    assert case["onset"] == "2024-04-30T18:30:00.123456Z"
    assert case["report_by"] == "2024-05-30T18:30:00.123456Z"
    assert case["overdue"] is False
    assert triage_events([event("A", onset=onset)], now=deadline + timedelta(microseconds=1))[0][
        "overdue"
    ]


def test_duplicate_root_ids_are_rejected_even_when_records_match():
    with pytest.raises(ValueError, match="DUPLICATE_EVENT_ID"):
        triage_events([event("root"), event("root")], now=UTC_NOW)


def test_self_cycle_and_longer_cycle_are_rejected():
    with pytest.raises(ValueError, match="FOLLOW_UP_CYCLE"):
        triage_events([event("A", follow_up_of="A")], now=UTC_NOW)
    with pytest.raises(ValueError, match="FOLLOW_UP_CYCLE"):
        triage_events(
            [
                event("A", follow_up_of="C"),
                event("B", follow_up_of="A"),
                event("C", follow_up_of="B"),
            ],
            now=UTC_NOW,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("subject_pseudonym", "pseudo-2"), ("event_code", "CODE-2")],
)
def test_a_chain_cannot_mix_subject_or_code(field, value):
    with pytest.raises(ValueError, match="MIXED_CASE"):
        triage_events(
            [event("root"), event("child", follow_up_of="root", **{field: value})],
            now=UTC_NOW,
        )


def test_missing_parent_and_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="MISSING_ROOT"):
        triage_events([event("child", follow_up_of="unknown")], now=UTC_NOW)
    with pytest.raises(ValueError, match="INVALID_NOW"):
        triage_events([], now=datetime(2024, 1, 1))
    with pytest.raises(ValueError, match="INVALID_ONSET"):
        triage_events([event("A", onset="2024-01-01T00:00:00")], now=UTC_NOW)


def test_unknown_seriousness_is_rejected():
    with pytest.raises(ValueError, match="INVALID_SERIOUSNESS"):
        triage_events([event("A", seriousness="severe")], now=UTC_NOW)


def test_valid_input_order_never_changes_cases_or_deadlines():
    events = [
        event("root", onset="2024-01-05T00:00:00Z"),
        event(
            "child",
            follow_up_of="root",
            onset="2024-01-01T00:00:00Z",
            seriousness="hospitalization",
            uncertain=True,
        ),
        event("other", onset="2024-01-01T00:00:00Z", source="source-a"),
    ]
    expected = triage_events(events, now=UTC_NOW)
    assert triage_events(list(reversed(events)), now=UTC_NOW) == expected
    assert triage_events([events[1], events[2], events[0]], now=UTC_NOW) == expected
    assert expected[0]["case_id"] == "other"
    assert expected[1]["report_by"] == "2024-01-16T00:00:00Z"
