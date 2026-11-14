"""Deterministic protocol-visit scheduling for synthetic trial data."""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ONE_DAY = timedelta(days=1)
_ZERO_DELTA = timedelta(0)


def _plain_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_day(value: object, field: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field} must contain datetime.date values")
    return value


def _add_months(original: date, offset: int) -> date:
    month_index = original.year * 12 + original.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    if not 1 <= year <= 9999:
        raise ValueError("OFFSET_OUT_OF_RANGE")
    month = zero_based_month + 1
    day = min(original.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _localize(day: date, hour: int, minute: int, zone: ZoneInfo) -> datetime:
    """Attach a zone, shifting gaps by their actual size and preferring fold=0."""
    wall = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=zone,
        fold=0,
    )
    offset_at_fold_zero = wall.utcoffset()
    offset_at_fold_one = wall.replace(fold=1).utcoffset()
    # ZoneInfo always supplies both offsets. A positive difference identifies a
    # gap; a negative difference is an ambiguous time, for which fold=0 wins.
    if offset_at_fold_zero is None or offset_at_fold_one is None:
        raise ValueError("INVALID_LOCAL_TIME")
    gap = offset_at_fold_one - offset_at_fold_zero
    if gap > _ZERO_DELTA:
        wall = (wall.replace(tzinfo=None) + gap).replace(tzinfo=zone, fold=0)
    return wall


def plan_visits(
    *,
    first_day: date,
    offsets_months: Iterable[int],
    hour: int,
    minute: int,
    timezone: str,
    closed_days: Iterable[date] | None = None,
    capacity: Mapping[date, int] | None = None,
) -> list[dict[str, object]]:
    """Plan visits in protocol order without modifying caller-owned mappings."""
    _require_day(first_day, "first_day")

    try:
        offsets = tuple(offsets_months)
    except TypeError as exc:
        raise TypeError("offsets_months must be an iterable of integers") from exc

    seen_offsets: set[int] = set()
    for offset in offsets:
        if not _plain_integer(offset) or offset < 0:
            raise ValueError("INVALID_OFFSET")
        if offset in seen_offsets:
            raise ValueError("DUPLICATE_OFFSET")
        seen_offsets.add(offset)

    if not _plain_integer(hour) or not 0 <= hour <= 23:
        raise ValueError("INVALID_HOUR")
    if not _plain_integer(minute) or not 0 <= minute <= 59:
        raise ValueError("INVALID_MINUTE")
    if not isinstance(timezone, str) or not timezone:
        raise ValueError("INVALID_TIMEZONE")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("INVALID_TIMEZONE") from exc

    closed: set[date] = set()
    if closed_days is not None:
        try:
            closed_iterator = iter(closed_days)
        except TypeError as exc:
            raise TypeError("closed_days must be an iterable of dates") from exc
        for closed_day in closed_iterator:
            closed.add(_require_day(closed_day, "closed_days"))

    remaining: dict[date, int] = {}
    if capacity is not None:
        if not isinstance(capacity, Mapping):
            raise TypeError("capacity must be a mapping of dates to slots")
        for capacity_day, slots in capacity.items():
            day = _require_day(capacity_day, "capacity")
            if not _plain_integer(slots) or slots < 0:
                raise ValueError("INVALID_CAPACITY")
            remaining[day] = slots

    visits: list[dict[str, object]] = []
    for offset in offsets:
        nominal_day = _add_months(first_day, offset)
        visit_day = nominal_day
        # Greedily choose the earliest feasible date, including the nominal
        # day and the 366 following days. All capacity changes stay private,
        # so a later UNSCHEDULABLE failure cannot expose a partial allocation.
        for days_after_nominal in range(367):
            if visit_day not in closed and remaining.get(visit_day, 1) != 0:
                break
            if days_after_nominal == 366:
                raise ValueError("UNSCHEDULABLE")
            try:
                visit_day += _ONE_DAY
            except OverflowError as exc:
                raise ValueError("UNSCHEDULABLE") from exc

        if visit_day in remaining:
            remaining[visit_day] -= 1

        try:
            local = _localize(visit_day, hour, minute, zone)
            utc = local.astimezone(datetime_timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("LOCAL_TIME_OUT_OF_RANGE") from exc

        visits.append(
            {
                "offset_months": offset,
                "local": local.isoformat(timespec="seconds"),
                "utc": utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
        )

    return visits


