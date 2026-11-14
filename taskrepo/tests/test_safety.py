from copy import deepcopy
from datetime import datetime, timezone

import pytest

from trialops.safety import triage_events


def event(event_id, **changes):
    value = {
        "event_id": event_id,
        "subject_pseudonym": "subject-001",
        "event_code": "SYNTH-01",
        "onset": "2024-01-10T10:00:00+02:00",
        "seriousness": "other",
        "source": "site-b",
    }
    value.update(changes)
    return value


def test_chained_followups_merge_offsets_sources_and_seriousness():
    events = [
        event(
            "E2",
            follow_up_of="E1",
            onset="2024-01-09T23:30:00-05:00",
            seriousness="hospitalization",
            source="site-a",
            uncertain=True,
        ),
        event("E3", follow_up_of="E2", onset="2024-01-11T00:00:00Z", seriousness="fatal"),
        event("E1"),
    ]
    original = deepcopy(events)
    deadline = datetime(2024, 1, 17, 4, 30, tzinfo=timezone.utc)

    assert triage_events(events, now=deadline) == [
        {
            "case_id": "E1",
            "subject_pseudonym": "subject-001",
            "event_code": "SYNTH-01",
            "onset": "2024-01-10T04:30:00Z",
            "seriousness": "fatal",
            "report_by": "2024-01-17T04:30:00Z",
            "sources": ["site-a", "site-b"],
            "event_ids": ["E1", "E2", "E3"],
            "uncertain": True,
            "overdue": False,
        }
    ]
    assert events == original
    assert triage_events(events, now=deadline + deadline.resolution)[0]["overdue"]


def test_cases_sort_by_onset_then_case_id():
    events = [
        event("B", onset="2024-02-01T00:00:00Z"),
        event("C", onset="2024-01-31T23:00:00Z"),
        event("A", onset="2024-02-01T00:00:00Z"),
    ]

    cases = triage_events(events, now=datetime(2024, 2, 1, tzinfo=timezone.utc))
    assert [case["case_id"] for case in cases] == ["C", "A", "B"]


def test_duplicate_ids_and_missing_roots_are_rejected():
    with pytest.raises(ValueError, match="DUPLICATE_EVENT_ID"):
        triage_events(
            [event("E1"), event("E1")],
            now=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="MISSING_ROOT"):
        triage_events(
            [event("E2", follow_up_of="absent")],
            now=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )


def test_cycles_and_mixed_case_fields_are_rejected():
    with pytest.raises(ValueError, match="FOLLOW_UP_CYCLE"):
        triage_events(
            [event("A", follow_up_of="B"), event("B", follow_up_of="A")],
            now=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="MIXED_CASE"):
        triage_events(
            [event("A"), event("B", follow_up_of="A", event_code="OTHER")],
            now=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )


def test_naive_now_and_onset_are_rejected():
    with pytest.raises(ValueError, match="INVALID_NOW"):
        triage_events([], now=datetime(2024, 1, 1))
    with pytest.raises(ValueError, match="INVALID_ONSET"):
        triage_events(
            [event("A", onset="2024-01-01T00:00:00")],
            now=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
