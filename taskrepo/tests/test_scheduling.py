from datetime import date, timedelta

import pytest

from trialops.scheduling import plan_visits


def test_month_end_targets_use_the_original_day_and_leap_year():
    visits = plan_visits(
        first_day=date(2024, 1, 31),
        offsets_months=[1, 2, 12],
        hour=9,
        minute=5,
        timezone="America/New_York",
    )

    assert [visit["offset_months"] for visit in visits] == [1, 2, 12]
    assert [visit["local"] for visit in visits] == [
        "2024-02-29T09:05:00-05:00",
        "2024-03-31T09:05:00-04:00",
        "2025-01-31T09:05:00-05:00",
    ]
    assert visits[0]["utc"] == "2024-02-29T14:05:00Z"


def test_new_york_spring_forward_moves_forward_by_the_gap():
    [visit] = plan_visits(
        first_day=date(2024, 3, 10),
        offsets_months=[0],
        hour=2,
        minute=30,
        timezone="America/New_York",
    )

    assert visit == {
        "offset_months": 0,
        "local": "2024-03-10T03:30:00-04:00",
        "utc": "2024-03-10T07:30:00Z",
    }


def test_new_york_fall_back_uses_fold_zero():
    [visit] = plan_visits(
        first_day=date(2024, 11, 3),
        offsets_months=[0],
        hour=1,
        minute=30,
        timezone="America/New_York",
    )

    assert visit["local"] == "2024-11-03T01:30:00-04:00"
    assert visit["utc"] == "2024-11-03T05:30:00Z"


def test_paris_gap_uses_the_resulting_utc_offset():
    [visit] = plan_visits(
        first_day=date(2024, 3, 31),
        offsets_months=[0],
        hour=2,
        minute=30,
        timezone="Europe/Paris",
    )

    assert visit["local"] == "2024-03-31T03:30:00+02:00"
    assert visit["utc"] == "2024-03-31T01:30:00Z"


def test_closures_and_shared_capacity_do_not_mutate_inputs():
    closed = {date(2024, 1, 31)} | {
        date(2024, 2, day) for day in range(1, 29)
    }
    capacity = {date(2024, 2, 29): 1}
    original_closed = closed.copy()

    visits = plan_visits(
        first_day=date(2024, 1, 31),
        offsets_months=[0, 1],
        hour=10,
        minute=0,
        timezone="America/New_York",
        closed_days=closed,
        capacity=capacity,
    )

    assert [visit["local"][:10] for visit in visits] == [
        "2024-02-29",
        "2024-03-01",
    ]
    assert capacity == {date(2024, 2, 29): 1}
    assert closed == original_closed


@pytest.mark.parametrize("offsets", [[-1], [0, 0], [True], [0.5]])
def test_invalid_offsets_are_rejected(offsets):
    with pytest.raises(ValueError):
        plan_visits(
            first_day=date(2024, 1, 1),
            offsets_months=offsets,
            hour=9,
            minute=0,
            timezone="America/New_York",
        )


@pytest.mark.parametrize(
    ("hour", "minute", "zone"),
    [(24, 0, "America/New_York"), (0, 60, "America/New_York"), (0, 0, "Not/A_Real_Zone")],
)
def test_invalid_times_and_zones_are_rejected(hour, minute, zone):
    with pytest.raises(ValueError):
        plan_visits(
            first_day=date(2024, 1, 1),
            offsets_months=[0],
            hour=hour,
            minute=minute,
            timezone=zone,
        )


def test_unschedulable_plan_is_atomic_and_does_not_consume_capacity():
    second_nominal = date(2024, 2, 1)
    closed = {second_nominal + timedelta(days=days) for days in range(367)}
    capacity = {date(2024, 1, 1): 1}

    with pytest.raises(ValueError, match="UNSCHEDULABLE"):
        plan_visits(
            first_day=date(2024, 1, 1),
            offsets_months=[0, 1],
            hour=9,
            minute=0,
            timezone="America/New_York",
            closed_days=closed,
            capacity=capacity,
        )
    assert capacity == {date(2024, 1, 1): 1}
