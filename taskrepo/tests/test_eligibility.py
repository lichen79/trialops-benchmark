from datetime import date

import pytest

from trialops.eligibility import evaluate_subject


def make_protocol():
    return {
        "min_age": 18,
        "max_age": 30,
        "required_consent_version": "v2",
        "consent_valid_from": "2024-01-01",
        "labs": {"A": [1, 3], "Z": [10, 20]},
        "exclusions": ["DO_NOT_ENROLL", "PRIOR"],
    }


def make_subject():
    return {
        "birth_date": "2000-06-15",
        "consents": [{"version": "v2", "signed_on": "2024-06-15"}],
        "labs": {"A": 2, "Z": 15},
        "flags": [],
    }


def test_same_day_consent_and_inclusive_age_and_lab_boundaries():
    protocol = make_protocol()
    protocol["min_age"] = protocol["max_age"] = 24
    subject = make_subject()
    subject["labs"] = {"A": 1, "Z": 20}

    assert evaluate_subject(subject, protocol, date(2024, 6, 15)) == {
        "eligible": True,
        "reasons": [],
        "consent_version": "v2",
    }


def test_reason_order_and_retired_wrong_consent_version():
    protocol = make_protocol()
    subject = make_subject()
    subject.update(
        birth_date="1970-01-01",
        consents=[{"version": "v1", "signed_on": "2023-12-31"}],
        flags=["PRIOR", "DO_NOT_ENROLL"],
        labs={"Z": 21, "A": 0},
    )

    result = evaluate_subject(subject, protocol, date(2024, 6, 15))

    assert result == {
        "eligible": False,
        "reasons": [
            "CONSENT_MISSING",
            "CONSENT_VERSION",
            "AGE_ABOVE_MAX",
            "EXCLUSION_DO_NOT_ENROLL",
            "EXCLUSION_PRIOR",
            "LAB_A_LOW",
            "LAB_Z_HIGH",
        ],
        "consent_version": "v1",
    }


def test_leap_day_birthday_is_computed_on_the_evaluation_date():
    protocol = make_protocol()
    protocol.update(min_age=24, max_age=24, labs={}, exclusions=[])
    subject = make_subject()
    subject.update(
        birth_date="2000-02-29",
        consents=[{"version": "v2", "signed_on": "2024-01-01"}],
        labs={},
    )

    before_birthday = evaluate_subject(subject, protocol, date(2024, 2, 28))
    on_birthday = evaluate_subject(subject, protocol, date(2024, 3, 1))

    assert before_birthday["reasons"] == ["AGE_BELOW_MIN"]
    assert on_birthday["eligible"] is True


def test_latest_consent_across_versions_never_falls_back_to_older_valid_consent():
    subject = make_subject()
    subject["consents"] = [
        {"version": "v9", "signed_on": "2025-01-01"},
        {"version": "v1", "signed_on": "2024-02-01"},
        {"version": "v2", "signed_on": "2024-01-01"},
    ]

    result = evaluate_subject(subject, make_protocol(), date(2024, 6, 15))

    assert result["consent_version"] == "v1"
    assert result["reasons"] == ["CONSENT_VERSION"]


def test_malformed_required_fields_raise_field_naming_value_errors():
    subject = make_subject()
    subject["birth_date"] = "not-a-date"
    with pytest.raises(ValueError, match="birth_date"):
        evaluate_subject(subject, make_protocol(), date(2024, 6, 15))

    subject = make_subject()
    del subject["labs"]["A"]
    with pytest.raises(ValueError, match="labs.A"):
        evaluate_subject(subject, make_protocol(), date(2024, 6, 15))

    with pytest.raises(ValueError, match="on"):
        evaluate_subject(make_subject(), make_protocol(), "2024-06-15")
