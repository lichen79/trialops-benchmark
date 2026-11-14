"""Deterministic follow-up triage for fictional safety-event records."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_SERIOUSNESS_RANK = {
    "other": 0,
    "hospitalization": 1,
    "life_threatening": 2,
    "fatal": 3,
}
_REPORTING_DAYS = {
    "fatal": 7,
    "life_threatening": 7,
    "hospitalization": 15,
    "other": 30,
}


@dataclass(frozen=True)
class _Event:
    event_id: str
    subject_pseudonym: str
    event_code: str
    onset: datetime
    seriousness: str
    source: str
    follow_up_of: str | None
    uncertain: bool


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"INVALID_{field.upper()}")
    return value


def _aware_utc(value: object, error_code: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(error_code)
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(error_code)
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError(error_code) from exc


def _parse_onset(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("INVALID_ONSET")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("INVALID_ONSET") from exc
    return _aware_utc(parsed, "INVALID_ONSET")


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def triage_events(events: Iterable[Mapping[str, object]], *, now: datetime) -> list[dict[str, object]]:
    """Merge follow-up chains and derive reporting deadlines, not diagnoses."""
    now_utc = _aware_utc(now, "INVALID_NOW")
    by_id: dict[str, _Event] = {}

    try:
        iterator = iter(events)
    except TypeError as exc:
        raise TypeError("events must be iterable") from exc

    for raw in iterator:
        if not isinstance(raw, Mapping):
            raise ValueError("INVALID_EVENT")
        event_id = _required_text(raw.get("event_id"), "event_id")
        if event_id in by_id:
            raise ValueError("DUPLICATE_EVENT_ID")

        seriousness = raw.get("seriousness")
        if not isinstance(seriousness, str) or seriousness not in _SERIOUSNESS_RANK:
            raise ValueError("INVALID_SERIOUSNESS")
        uncertain = raw.get("uncertain", False)
        if not isinstance(uncertain, bool):
            raise ValueError("INVALID_UNCERTAIN")

        parent = raw.get("follow_up_of")
        if parent is not None:
            parent = _required_text(parent, "follow_up_of")

        by_id[event_id] = _Event(
            event_id=event_id,
            subject_pseudonym=_required_text(
                raw.get("subject_pseudonym"), "subject_pseudonym"
            ),
            event_code=_required_text(raw.get("event_code"), "event_code"),
            onset=_parse_onset(raw.get("onset")),
            seriousness=seriousness,
            source=_required_text(raw.get("source"), "source"),
            follow_up_of=parent,
            uncertain=uncertain,
        )

    for event in by_id.values():
        if event.follow_up_of is not None and event.follow_up_of not in by_id:
            raise ValueError("MISSING_ROOT")

    # Cache each resolved root. The per-path set catches self-links and cycles
    # even when events arrive in follow-up-before-root order.
    roots: dict[str, str] = {}
    for event_id in by_id:
        path: list[str] = []
        seen: set[str] = set()
        current = event_id
        while True:
            if current in roots:
                root = roots[current]
                break
            if current in seen:
                raise ValueError("FOLLOW_UP_CYCLE")
            seen.add(current)
            path.append(current)
            parent = by_id[current].follow_up_of
            if parent is None:
                root = current
                break
            current = parent
        for member_id in path:
            roots[member_id] = root

    grouped: dict[str, list[_Event]] = {}
    for event_id, event in by_id.items():
        root_id = roots[event_id]
        root_event = by_id[root_id]
        if (
            event.subject_pseudonym != root_event.subject_pseudonym
            or event.event_code != root_event.event_code
        ):
            raise ValueError("MIXED_CASE")
        grouped.setdefault(root_id, []).append(event)

    sortable_cases: list[tuple[datetime, str, dict[str, object]]] = []
    for root_id, members in grouped.items():
        root_event = by_id[root_id]
        earliest_onset = min(member.onset for member in members)
        highest_seriousness = max(
            members, key=lambda member: _SERIOUSNESS_RANK[member.seriousness]
        ).seriousness
        try:
            report_by = earliest_onset + timedelta(
                days=_REPORTING_DAYS[highest_seriousness]
            )
        except OverflowError as exc:
            raise ValueError("REPORT_BY_OUT_OF_RANGE") from exc

        case = {
            "case_id": root_id,
            "subject_pseudonym": root_event.subject_pseudonym,
            "event_code": root_event.event_code,
            "onset": _format_utc(earliest_onset),
            "seriousness": highest_seriousness,
            "report_by": _format_utc(report_by),
            "sources": sorted({member.source for member in members}),
            "event_ids": sorted(member.event_id for member in members),
            "uncertain": any(member.uncertain for member in members),
            "overdue": report_by < now_utc,
        }
        sortable_cases.append((earliest_onset, root_id, case))

    sortable_cases.sort(key=lambda item: (item[0], item[1]))
    return [case for _, _, case in sortable_cases]
