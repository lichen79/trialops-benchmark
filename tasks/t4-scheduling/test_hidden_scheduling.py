from copy import deepcopy
from datetime import date, timedelta
from types import MappingProxyType

import pytest

from trialops.scheduling import book_subject_visits, plan_visits


def plan(first, offsets, **changes):
    arguments = {
        "first_day": first,
        "offsets_months": offsets,
        "hour": 9,
        "minute": 0,
        "timezone": "America/New_York",
    }
    arguments.update(changes)
    return plan_visits(**arguments)


ON = date(2024, 6, 15)


def screening_subject(**changes):
    value = {
        "birth_date": "2000-06-15",
        "consents": [{"version": "v2", "signed_on": "2024-01-01"}],
        "labs": {},
        "flags": [],
    }
    value.update(changes)
    return value


def screening_protocol(**changes):
    value = {
        "min_age": 18,
        "max_age": 65,
        "required_consent_version": "v2",
        "consent_valid_from": "2024-01-01",
        "labs": {},
        "exclusions": [],
    }
    value.update(changes)
    return value


def book(first, offsets, capacity, *, person=None, rules=None, **changes):
    arguments = {
        "on": ON,
        "first_day": first,
        "offsets_months": offsets,
        "hour": 9,
        "minute": 0,
        "timezone": "America/New_York",
        "capacity": capacity,
    }
    arguments.update(changes)
    return book_subject_visits(
        screening_subject() if person is None else person,
        screening_protocol() if rules is None else rules,
        **arguments,
    )


def test_non_leap_clamping_never_sticks_to_a_short_month():
    visits = plan(date(2023, 1, 31), [1, 2, 12, 13])
    assert [visit["local"][:10] for visit in visits] == [
        "2023-02-28",
        "2023-03-31",
        "2024-01-31",
        "2024-02-29",
    ]


def test_zero_capacity_moves_forward_and_missing_dates_are_unconstrained():
    capacity = {date(2024, 5, 1): 0, date(2024, 5, 2): 1}
    [visit] = plan(date(2024, 5, 1), [0], capacity=capacity)
    assert visit["local"] == "2024-05-02T09:00:00-04:00"
    assert capacity == {date(2024, 5, 1): 0, date(2024, 5, 2): 1}

    # The caller's one slot is replenished for an independent invocation.
    assert plan(date(2024, 5, 1), [0], capacity=capacity) == [visit]


def test_capacity_is_consumed_in_protocol_order_not_chronological_order():
    closed = {date(2024, 1, 31)} | {
        date(2024, 2, day) for day in range(1, 29)
    }
    visits = plan(
        date(2024, 1, 31),
        [1, 0],
        closed_days=closed,
        capacity={date(2024, 2, 29): 1},
    )
    assert [visit["offset_months"] for visit in visits] == [1, 0]
    assert [visit["local"][:10] for visit in visits] == [
        "2024-02-29",
        "2024-03-01",
    ]


def test_closure_onto_spring_gap_preserves_the_requested_wall_time_rule():
    [visit] = plan(
        date(2024, 3, 9),
        [0],
        hour=2,
        minute=30,
        timezone="America/New_York",
        closed_days={date(2024, 3, 9)},
    )
    assert visit["local"] == "2024-03-10T03:30:00-04:00"
    assert visit["utc"] == "2024-03-10T07:30:00Z"


def test_closure_onto_ambiguous_time_still_uses_fold_zero():
    [visit] = plan(
        date(2024, 11, 2),
        [0],
        hour=1,
        minute=30,
        timezone="America/New_York",
        closed_days=[date(2024, 11, 2)],
    )
    assert visit["local"] == "2024-11-03T01:30:00-04:00"
    assert visit["utc"] == "2024-11-03T05:30:00Z"


def test_paris_fall_back_uses_the_eastward_fold_zero_offset():
    [visit] = plan(
        date(2024, 10, 27),
        [0],
        hour=2,
        minute=30,
        timezone="Europe/Paris",
    )
    assert visit["local"] == "2024-10-27T02:30:00+02:00"
    assert visit["utc"] == "2024-10-27T00:30:00Z"


def test_offset_generators_and_empty_offsets_are_supported():
    assert plan(date(2024, 1, 1), (offset for offset in [0, 2]))[1][
        "local"
    ] == "2024-03-01T09:00:00-05:00"
    assert plan(date(2024, 1, 1), []) == []


@pytest.mark.parametrize("slots", [-1, True, 1.5, "1"])
def test_invalid_capacity_values_are_rejected(slots):
    with pytest.raises(ValueError):
        plan(
            date(2024, 1, 1),
            [0],
            capacity={date(2024, 1, 1): slots},
        )


def test_duplicate_generator_offsets_and_large_offsets_are_rejected():
    with pytest.raises(ValueError):
        plan(date(2024, 1, 1), (offset for offset in [0, 0]))
    with pytest.raises(ValueError):
        plan(date(9999, 12, 31), [1])


def test_nominal_day_plus_366_is_the_last_available_candidate():
    nominal = date(2024, 1, 1)
    last_allowed = nominal + timedelta(days=366)
    closed = {nominal + timedelta(days=days) for days in range(366)}
    [visit] = plan(nominal, [0], closed_days=closed)
    assert visit["local"] == "2025-01-01T09:00:00-05:00"
    assert last_allowed == date(2025, 1, 1)


def test_unschedulable_later_visit_is_atomic_and_cannot_use_day_367():
    second_nominal = date(2024, 2, 1)
    closed = {second_nominal + timedelta(days=days) for days in range(367)}
    capacity = {date(2024, 1, 1): 1}
    with pytest.raises(ValueError, match="UNSCHEDULABLE"):
        plan(
            date(2024, 1, 1),
            [0, 1],
            closed_days=closed,
            capacity=capacity,
        )
    assert capacity == {date(2024, 1, 1): 1}


def test_sequential_bookings_share_one_persistent_slot():
    first_day = date(2024, 5, 1)
    capacity = {first_day: 1}

    first = book(first_day, [0], capacity)

    assert first == [
        {
            "offset_months": 0,
            "local": "2024-05-01T09:00:00-04:00",
            "utc": "2024-05-01T13:00:00Z",
        }
    ]
    assert capacity == {first_day: 0}

    second = book(first_day, [0], capacity)

    assert second == [
        {
            "offset_months": 0,
            "local": "2024-05-02T09:00:00-04:00",
            "utc": "2024-05-02T13:00:00Z",
        }
    ]
    assert capacity == {first_day: 0}


def test_multiple_visits_compete_and_each_consumes_a_listed_slot():
    first_day = date(2024, 1, 31)
    shared_day = date(2024, 4, 1)
    closed = {first_day + timedelta(days=days) for days in range(61)}
    capacity = {shared_day: 2}

    visits = book(
        first_day,
        [2, 1, 0],
        capacity,
        hour=10,
        closed_days=closed,
    )

    assert visits == [
        {
            "offset_months": 2,
            "local": "2024-04-01T10:00:00-04:00",
            "utc": "2024-04-01T14:00:00Z",
        },
        {
            "offset_months": 1,
            "local": "2024-04-01T10:00:00-04:00",
            "utc": "2024-04-01T14:00:00Z",
        },
        {
            "offset_months": 0,
            "local": "2024-04-02T10:00:00-04:00",
            "utc": "2024-04-02T14:00:00Z",
        },
    ]
    assert capacity == {shared_day: 0}


def test_later_unschedulable_rolls_back_only_the_current_call():
    first_day = date(2024, 1, 1)
    second_nominal = date(2024, 2, 1)
    capacity = {first_day: 2}
    assert book(first_day, [0], capacity)[0]["local"].startswith("2024-01-01")
    assert capacity == {first_day: 1}

    closed = {second_nominal + timedelta(days=days) for days in range(367)}
    with pytest.raises(ValueError) as caught:
        book(first_day, [0, 1], capacity, closed_days=closed)

    assert str(caught.value) == "UNSCHEDULABLE"
    assert capacity == {first_day: 1}


def test_ineligibility_is_ordered_and_does_not_start_planning():
    person = screening_subject(
        birth_date="2010-06-14",
        consents=[{"version": "v1", "signed_on": "2023-01-01"}],
        labs={"z": 3, "A": 9},
        flags=["A_FLAG", "Z_FLAG"],
    )
    rules = screening_protocol(
        labs={"z": [0, 2], "A": [10, 20]},
        exclusions=["Z_FLAG", "A_FLAG"],
    )
    capacity = {date(2024, 6, 15): 1}
    consumed_offsets = []

    def offsets():
        consumed_offsets.append(0)
        yield 0

    with pytest.raises(ValueError) as caught:
        book(
            date(2024, 6, 15),
            offsets(),
            capacity,
            person=person,
            rules=rules,
        )

    assert str(caught.value) == (
        "INELIGIBLE:CONSENT_MISSING,CONSENT_VERSION,AGE_BELOW_MIN,"
        "EXCLUSION_Z_FLAG,EXCLUSION_A_FLAG,LAB_A_LOW,LAB_z_HIGH"
    )
    assert capacity == {date(2024, 6, 15): 1}
    assert consumed_offsets == []


def test_inclusive_eligibility_boundaries_allow_a_reservation():
    person = screening_subject(
        consents=[{"version": "v2", "signed_on": "2024-06-15"}],
        labs={"A": 10, "z": 2},
    )
    rules = screening_protocol(
        min_age=24,
        max_age=24,
        consent_valid_from="2024-06-15",
        labs={"A": [10, 20], "z": [0, 2]},
    )
    booked_day = date(2024, 6, 15)
    capacity = {booked_day: 1}

    visits = book(
        booked_day,
        [0],
        capacity,
        person=person,
        rules=rules,
    )

    assert visits == [
        {
            "offset_months": 0,
            "local": "2024-06-15T09:00:00-04:00",
            "utc": "2024-06-15T13:00:00Z",
        }
    ]
    assert capacity == {booked_day: 0}


@pytest.mark.parametrize(
    (
        "first",
        "offsets",
        "hour",
        "minute",
        "zone",
        "closed",
        "capacity",
        "expected",
        "remaining",
    ),
    [
        (
            date(2024, 1, 31),
            [1, 2, 12],
            9,
            5,
            "America/New_York",
            None,
            {date(2023, 1, 31): 0},
            [
                {
                    "offset_months": 1,
                    "local": "2024-02-29T09:05:00-05:00",
                    "utc": "2024-02-29T14:05:00Z",
                },
                {
                    "offset_months": 2,
                    "local": "2024-03-31T09:05:00-04:00",
                    "utc": "2024-03-31T13:05:00Z",
                },
                {
                    "offset_months": 12,
                    "local": "2025-01-31T09:05:00-05:00",
                    "utc": "2025-01-31T14:05:00Z",
                },
            ],
            {date(2023, 1, 31): 0},
        ),
        (
            date(2024, 3, 9),
            [0],
            2,
            30,
            "America/New_York",
            {date(2024, 3, 9)},
            {date(2024, 3, 10): 1},
            [
                {
                    "offset_months": 0,
                    "local": "2024-03-10T03:30:00-04:00",
                    "utc": "2024-03-10T07:30:00Z",
                }
            ],
            {date(2024, 3, 10): 0},
        ),
        (
            date(2024, 11, 2),
            [0],
            1,
            30,
            "America/New_York",
            [date(2024, 11, 2)],
            {date(2024, 11, 3): 2},
            [
                {
                    "offset_months": 0,
                    "local": "2024-11-03T01:30:00-04:00",
                    "utc": "2024-11-03T05:30:00Z",
                }
            ],
            {date(2024, 11, 3): 1},
        ),
        (
            date(2024, 10, 27),
            [0],
            2,
            30,
            "Europe/Paris",
            None,
            {date(2024, 10, 27): 1},
            [
                {
                    "offset_months": 0,
                    "local": "2024-10-27T02:30:00+02:00",
                    "utc": "2024-10-27T00:30:00Z",
                }
            ],
            {date(2024, 10, 27): 0},
        ),
    ],
    ids=["month-end", "closure-onto-gap", "new-york-fold", "paris-fold"],
)
def test_booking_preserves_calendar_closure_and_zone_rules(
    first,
    offsets,
    hour,
    minute,
    zone,
    closed,
    capacity,
    expected,
    remaining,
):
    visits = book(
        first,
        offsets,
        capacity,
        hour=hour,
        minute=minute,
        timezone=zone,
        closed_days=closed,
    )

    assert visits == expected
    assert capacity == remaining


@pytest.mark.parametrize("capacity", [None, MappingProxyType({})])
def test_booking_requires_an_actual_dict_for_capacity(capacity):
    with pytest.raises(TypeError, match="capacity"):
        book(date(2024, 1, 1), [0], capacity)


def test_booking_does_not_mutate_screening_inputs_or_closed_days():
    person = screening_subject(labs={"A": 10}, flags=["REPORTED"])
    rules = screening_protocol(labs={"A": [0, 20]}, exclusions=["OTHER"])
    closed = [date(2024, 1, 1)]
    original = deepcopy((person, rules, closed))
    capacity = {date(2024, 1, 2): 1}

    visits = book(
        date(2024, 1, 1),
        [0],
        capacity,
        person=person,
        rules=rules,
        closed_days=closed,
    )

    assert visits[0]["local"] == "2024-01-02T09:00:00-05:00"
    assert capacity == {date(2024, 1, 2): 0}
    assert (person, rules, closed) == original
