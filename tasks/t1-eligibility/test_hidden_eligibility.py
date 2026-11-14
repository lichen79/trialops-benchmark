from copy import deepcopy
from datetime import date, datetime

import pytest

from trialops.eligibility import evaluate_subject


ON = date(2024, 6, 15)


def protocol(**changes):
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


def subject(**changes):
    value = {
        "birth_date": "2000-06-15",
        "consents": [{"version": "v2", "signed_on": "2024-01-01"}],
        "labs": {},
        "flags": [],
    }
    value.update(changes)
    return value


def test_only_future_consents_do_not_select_a_version():
    value = subject(
        consents=[{"version": "v2", "signed_on": "2024-06-16"}]
    )
    assert evaluate_subject(value, protocol(), ON) == {
        "eligible": False,
        "reasons": ["CONSENT_MISSING"],
        "consent_version": None,
    }


def test_latest_consent_across_all_versions_never_falls_back():
    value = subject(
        consents=[
            {"version": "v1", "signed_on": "2024-03-01"},
            {"version": "v2", "signed_on": "2024-02-01"},
            {"version": "future", "signed_on": "2025-01-01"},
        ]
    )
    result = evaluate_subject(value, protocol(), ON)
    assert result["consent_version"] == "v1"
    assert result["reasons"] == ["CONSENT_VERSION"]


def test_retired_wrong_consent_has_both_consent_reasons_in_order():
    value = subject(
        birth_date="2010-06-14",
        consents=[{"version": "v1", "signed_on": "2023-01-01"}],
    )
    assert evaluate_subject(value, protocol(), ON)["reasons"] == [
        "CONSENT_MISSING",
        "CONSENT_VERSION",
        "AGE_BELOW_MIN",
    ]


def test_age_changes_on_birthday_not_at_start_of_birth_year():
    rules = protocol(min_age=24, max_age=24)
    value = subject(birth_date="2000-06-15")
    assert evaluate_subject(value, rules, date(2024, 6, 14))["reasons"] == [
        "AGE_BELOW_MIN"
    ]
    assert evaluate_subject(value, rules, ON)["eligible"] is True

    value["birth_date"] = "1959-06-15"
    assert evaluate_subject(value, rules, ON)["reasons"] == ["AGE_ABOVE_MAX"]


def test_leap_day_birthday_in_leap_and_nonleap_years():
    value = subject(
        birth_date="2000-02-29",
        consents=[{"version": "v2", "signed_on": "2020-01-01"}],
    )
    rules = protocol(min_age=21, max_age=21, consent_valid_from="2020-01-01")
    assert evaluate_subject(value, rules, date(2021, 2, 28))["reasons"] == [
        "AGE_BELOW_MIN"
    ]
    assert evaluate_subject(value, rules, date(2021, 3, 1))["eligible"] is True
    rules = protocol(min_age=24, max_age=24, consent_valid_from="2020-01-01")
    assert evaluate_subject(value, rules, date(2024, 2, 29))["eligible"] is True


def test_lab_reasons_are_name_sorted_and_bounds_are_inclusive():
    rules = protocol(labs={"z": [0, 2], "A": [10, 20], "m": [-1, 1]})
    value = subject(labs={"z": 3, "A": 9, "m": 1})
    assert evaluate_subject(value, rules, ON)["reasons"] == [
        "LAB_A_LOW",
        "LAB_z_HIGH",
    ]
    value["labs"] = {"z": 2, "A": 10, "m": -1}
    assert evaluate_subject(value, rules, ON)["eligible"] is True


def test_exclusions_follow_protocol_order_not_flag_order_or_sorting():
    rules = protocol(exclusions=["Z_FLAG", "A_FLAG", "MIDDLE"])
    value = subject(flags=["A_FLAG", "MIDDLE", "Z_FLAG"])
    assert evaluate_subject(value, rules, ON)["reasons"] == [
        "EXCLUSION_Z_FLAG",
        "EXCLUSION_A_FLAG",
        "EXCLUSION_MIDDLE",
    ]


@pytest.mark.parametrize("field", ["birth_date", "consents", "labs", "flags"])
def test_missing_subject_fields_are_value_errors(field):
    value = subject()
    del value[field]
    with pytest.raises(ValueError, match=field):
        evaluate_subject(value, protocol(), ON)


@pytest.mark.parametrize(
    "field",
    [
        "min_age",
        "max_age",
        "required_consent_version",
        "consent_valid_from",
        "labs",
        "exclusions",
    ],
)
def test_missing_protocol_fields_are_value_errors(field):
    rules = protocol()
    del rules[field]
    with pytest.raises(ValueError, match=field):
        evaluate_subject(subject(), rules, ON)


def test_malformed_consents_labs_and_on_raise_value_errors():
    value = subject(consents=[{"version": "v2"}])
    with pytest.raises(ValueError, match="signed_on"):
        evaluate_subject(value, protocol(), ON)

    value = subject(labs={"A": float("nan")})
    with pytest.raises(ValueError, match="labs.A"):
        evaluate_subject(value, protocol(labs={"A": [0, 1]}), ON)

    with pytest.raises(ValueError, match="protocol.labs.A"):
        evaluate_subject(subject(labs={"A": 1}), protocol(labs={"A": [2]}), ON)

    with pytest.raises(ValueError, match="on"):
        evaluate_subject(subject(), protocol(), datetime(2024, 6, 15))


def test_evaluation_does_not_mutate_subject_or_protocol():
    value = subject(
        flags=["X"], labs={"A": 1}, consents=[
            {"version": "v1", "signed_on": "2024-02-01"},
            {"version": "v2", "signed_on": "2024-01-01"},
        ]
    )
    rules = protocol(labs={"A": [0, 1]}, exclusions=["X"])
    original = deepcopy((value, rules))
    evaluate_subject(value, rules, ON)
    assert (value, rules) == original
