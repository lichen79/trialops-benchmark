from copy import deepcopy
from datetime import date
import hashlib
import hmac
import json

import pytest

from trialops.privacy import export_subjects


ON = date(2024, 6, 15)


def make_subject(subject_id="subject-one", **changes):
    record = {
        "id": subject_id,
        "name": "Fictional Person",
        "birth_date": "1990-04-03",
        "email": "person@example.test",
        "country": "NZ",
        "visits": ["2024-05-01", "2024-01-02"],
        "consents": [{"purpose": "research", "signed_on": "2024-06-15"}],
        "purge_after": "2024-06-15",
    }
    record.update(changes)
    return record


def pseudonym(subject_id, *, purpose="research", secret="test-secret"):
    message = f"{purpose}:{subject_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:16]


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def test_active_boundaries_sorting_schema_and_canonical_manifest():
    subjects = [make_subject("subject-b"), make_subject("subject-a")]
    original = deepcopy(subjects)

    result = export_subjects(subjects, purpose="research", secret="test-secret", on=ON)

    expected_rows = [
        {
            "pseudonym": pseudonym("subject-a"),
            "country": "NZ",
            "birth_year": 1990,
            "visits": ["2024-01-02", "2024-05-01"],
        },
        {
            "pseudonym": pseudonym("subject-b"),
            "country": "NZ",
            "birth_year": 1990,
            "visits": ["2024-01-02", "2024-05-01"],
        },
    ]
    expected_rows.sort(key=lambda row: row["pseudonym"])
    assert result["rows"] == expected_rows
    assert result["manifest"] == {
        "algorithm": "sha256",
        "count": 2,
        "sha256": hashlib.sha256(canonical(expected_rows).encode("utf-8")).hexdigest(),
    }
    assert subjects == original


def test_withdrawal_on_on_is_inactive_but_later_withdrawal_is_active():
    withdrawn = make_subject("withdrawn")
    withdrawn["consents"][0]["withdrawn_on"] = "2024-06-15"
    active = make_subject("active")
    active["consents"][0]["withdrawn_on"] = "2024-06-16"
    expired = make_subject("expired", purge_after="2024-06-14")

    result = export_subjects(
        [withdrawn, active, expired], purpose="research", secret="test-secret", on=ON
    )

    assert [row["pseudonym"] for row in result["rows"]] == [pseudonym("active")]
    assert result["manifest"]["count"] == 1


def test_empty_input_has_canonical_empty_manifest():
    assert export_subjects([], purpose="research", secret="test-secret", on=ON) == {
        "rows": [],
        "manifest": {
            "algorithm": "sha256",
            "count": 0,
            "sha256": hashlib.sha256(b"[]").hexdigest(),
        },
    }


def test_duplicate_ids_and_bad_dates_have_identifier_free_errors():
    duplicate = make_subject("duplicate")
    with pytest.raises(ValueError) as caught:
        export_subjects(
            [duplicate, deepcopy(duplicate)],
            purpose="research",
            secret="test-secret",
            on=ON,
        )
    assert str(caught.value) == "DUPLICATE_SUBJECT_ID"
    assert "person@example.test" not in str(caught.value)

    malformed = make_subject("sensitive-id", birth_date="person@example.test")
    with pytest.raises(ValueError) as caught:
        export_subjects(
            [malformed], purpose="research", secret="test-secret", on=ON
        )
    message = str(caught.value)
    assert "birth_date" in message
    for identifier in ("sensitive-id", "Fictional Person", "person@example.test", "test-secret"):
        assert identifier not in message


def test_direct_identifiers_are_absent_from_output_values():
    result = export_subjects(
        [make_subject("sensitive-id")],
        purpose="research",
        secret="test-secret",
        on=ON,
    )
    serialized = canonical(result)
    for identifier in (
        "sensitive-id",
        "Fictional Person",
        "person@example.test",
        "1990-04-03",
        "2024-06-15",
        "test-secret",
    ):
        assert identifier not in serialized
    assert set(result["rows"][0]) == {"pseudonym", "country", "birth_year", "visits"}


def test_id_allowlist_accepts_ascii_word_dash_and_rejects_other_text():
    allowed = make_subject("AZaz09_-")
    result = export_subjects(
        [allowed], purpose="research", secret="test-secret", on=ON
    )
    assert result["rows"][0]["pseudonym"] == pseudonym("AZaz09_-")

    for invalid_id in ("has space", "colon:id", "unicode-é", "slash/id"):
        with pytest.raises(ValueError) as caught:
            export_subjects(
                [make_subject(invalid_id)],
                purpose="research",
                secret="test-secret",
                on=ON,
            )
        assert "id" in str(caught.value)
        assert invalid_id not in str(caught.value)
