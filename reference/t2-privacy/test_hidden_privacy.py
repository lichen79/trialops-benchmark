from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest

from trialops.governance import apply_reuse_action
from trialops.privacy import execute_reuse_export, export_subjects


ON = date(2024, 6, 15)


def subject(subject_id="id-one", **changes):
    value = {
        "id": subject_id,
        "name": "Example Name",
        "birth_date": "1988-12-31",
        "email": "private@example.test",
        "country": "Côte d’Ivoire",
        "visits": ["2024-03-01", "2023-12-31", "2024-03-01"],
        "consents": [{"purpose": "research", "signed_on": "2024-01-01"}],
        "purge_after": "2025-01-01",
    }
    value.update(changes)
    return value


def export(subjects, **changes):
    arguments = {"purpose": "research", "secret": "top-secret", "on": ON}
    arguments.update(changes)
    return export_subjects(subjects, **arguments)


def expected_pseudonym(subject_id, purpose="research", secret="top-secret"):
    return hmac.new(
        secret.encode("utf-8"),
        f"{purpose}:{subject_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]


def test_any_matching_active_consent_allows_export():
    value = subject(
        consents=[
            {"purpose": "other", "signed_on": "2020-01-01"},
            {"purpose": "research", "signed_on": "2024-06-16"},
            {
                "purpose": "research",
                "signed_on": "2023-01-01",
                "withdrawn_on": "2024-06-15",
            },
            {
                "purpose": "research",
                "signed_on": "2024-01-01",
                "withdrawn_on": "2024-06-16",
            },
        ]
    )
    result = export([value])
    assert result["manifest"]["count"] == 1
    assert result["rows"][0]["pseudonym"] == expected_pseudonym("id-one")


def test_null_withdrawal_is_active_and_signed_after_on_is_not():
    active = subject(consents=[{"purpose": "research", "signed_on": "2024-06-15", "withdrawn_on": None}])
    future = subject("id-two", consents=[{"purpose": "research", "signed_on": "2024-06-16"}])
    result = export([active, future])
    assert [row["pseudonym"] for row in result["rows"]] == [expected_pseudonym("id-one")]


def test_expired_and_all_withdrawn_subjects_are_omitted():
    expired = subject("expired", purge_after="2024-06-14")
    withdrawn = subject(
        "withdrawn",
        consents=[{"purpose": "research", "signed_on": "2024-01-01", "withdrawn_on": "2024-06-15"}],
    )
    wrong_purpose = subject("wrong", consents=[{"purpose": "other", "signed_on": "2024-01-01"}])
    assert export([expired, withdrawn, wrong_purpose])["manifest"] == {
        "algorithm": "sha256",
        "count": 0,
        "sha256": hashlib.sha256(b"[]").hexdigest(),
    }


def test_duplicate_visits_are_retained_and_sorted_and_unicode_hashes_canonically():
    result = export([subject()])
    row = result["rows"][0]
    assert row["visits"] == ["2023-12-31", "2024-03-01", "2024-03-01"]
    assert row["country"] == "Côte d’Ivoire"
    canonical = json.dumps(
        result["rows"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert result["manifest"]["sha256"] == hashlib.sha256(canonical).hexdigest()


def test_pseudonyms_are_purpose_and_secret_scoped():
    value = subject(
        consents=[
            {"purpose": "research", "signed_on": "2024-01-01"},
            {"purpose": "other", "signed_on": "2024-01-01"},
        ]
    )
    research = export([value])["rows"][0]["pseudonym"]
    other = export([value], purpose="other")["rows"][0]["pseudonym"]
    different_secret = export([value], secret="another-secret")["rows"][0]["pseudonym"]
    assert research == expected_pseudonym("id-one")
    assert other == expected_pseudonym("id-one", purpose="other")
    assert different_secret == expected_pseudonym("id-one", secret="another-secret")
    assert len({research, other, different_secret}) == 3


def test_duplicate_ids_are_rejected_even_when_records_would_be_excluded():
    first = subject("same-id", consents=[{"purpose": "other", "signed_on": "2020-01-01"}])
    second = deepcopy(first)
    with pytest.raises(ValueError) as caught:
        export([first, second])
    assert str(caught.value) == "DUPLICATE_SUBJECT_ID"
    assert "same-id" not in str(caught.value)


def test_invalid_dates_are_not_skipped_when_record_would_be_excluded():
    value = subject(
        "private-id",
        birth_date="private@example.test",
        consents=[{"purpose": "other", "signed_on": "2020-01-01"}],
    )
    with pytest.raises(ValueError) as caught:
        export([value])
    message = str(caught.value)
    assert "birth_date" in message
    for direct_value in ("private-id", "Example Name", "private@example.test", "top-secret"):
        assert direct_value not in message


@pytest.mark.parametrize(
    "field,value",
    [
        ("visits", ["2024-02-30"]),
        ("purge_after", "not-a-date"),
        ("consents", [{"purpose": "research", "signed_on": "bad"}]),
        ("consents", [{"purpose": "research", "signed_on": "2024-01-01", "withdrawn_on": "bad"}]),
    ],
)
def test_all_date_fields_are_validated(field, value):
    record = subject()
    record[field] = value
    with pytest.raises(ValueError):
        export([record])


@pytest.mark.parametrize("keyword", ["purpose", "secret"])
def test_empty_purpose_and_secret_are_rejected_without_echoing_configuration(keyword):
    with pytest.raises(ValueError) as caught:
        export([subject()], **{keyword: ""})
    assert keyword in str(caught.value)
    assert "Example Name" not in str(caught.value)
    assert "private@example.test" not in str(caught.value)


def test_invalid_on_and_empty_iterable_behavior():
    assert export(iter([]))["rows"] == []
    with pytest.raises(ValueError, match="on"):
        export([], on=datetime(2024, 6, 15))


def test_export_never_mutates_records_or_nested_consents():
    value = subject(
        consents=[
            {"purpose": "research", "signed_on": "2024-01-01", "withdrawn_on": "2024-06-16"},
            {"purpose": "other", "signed_on": "2024-01-01"},
        ]
    )
    original = deepcopy(value)
    export([value])
    assert value == original


@pytest.mark.parametrize(
    "invalid_id",
    ["", "has space", "colon:id", "dot.id", "unicode-é", "slash/id", None, 123],
)
def test_ids_use_the_ascii_allowlist_without_echoing_invalid_values(invalid_id):
    with pytest.raises(ValueError) as caught:
        export([subject(invalid_id)])
    assert "id" in str(caught.value)
    if isinstance(invalid_id, str) and invalid_id:
        assert invalid_id not in str(caught.value)


def test_independent_utf8_hmac_and_manifest_golden_values():
    purpose = "rešearch-研究"
    value = subject(
        "golden_ID-01",
        birth_date="1990-04-03",
        country="Côte d’Ivoire",
        visits=["2024-01-02"],
        consents=[{"purpose": purpose, "signed_on": "2024-01-01"}],
    )

    result = export([value], purpose=purpose, secret="sëcret-雪")

    assert result == {
        "rows": [
            {
                "pseudonym": "5954563ed92629b6",
                "country": "Côte d’Ivoire",
                "birth_year": 1990,
                "visits": ["2024-01-02"],
            }
        ],
        "manifest": {
            "algorithm": "sha256",
            "count": 1,
            "sha256": "93c450fdf38f0252fd525b9af3e2017bc7701468eac2d80ac688a21818e75346",
        },
    }


EXECUTION_NOW = datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc)


def reuse_request(status="draft", **changes):
    value = {
        "id": "reuse-export-001",
        "requester": "alice",
        "reviewer": "bob",
        "purpose": "research",
        "status": status,
        "retention_until": "2025-12-31",
        "privacy_plan": {
            "algorithm": "hmac-sha256",
            "domain": "research",
            "fields": ["pseudonym", "country", "birth_year", "visits"],
        },
        "metadata": {
            "representative": True,
            "complete": True,
            "quality_score": 0.97,
        },
        "history": [],
    }
    value.update(changes)
    return value


def approved_request(**changes):
    changes.setdefault("approval_expires_on", "2025-02-15")
    return reuse_request("approved", **changes)


def exportable_subject(subject_id="id-one", **changes):
    changes.setdefault("purge_after", "2025-12-31")
    return subject(subject_id, **changes)


def test_submit_approve_execute_returns_the_export_and_complete_history():
    draft = reuse_request()
    original_draft = deepcopy(draft)
    submitted = apply_reuse_action(
        draft,
        "submit",
        "alice",
        datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
    )
    approved = apply_reuse_action(
        submitted,
        "approve",
        "bob",
        datetime(2025, 1, 2, 10, 0, tzinfo=timezone.utc),
    )
    original_approved = deepcopy(approved)
    consumed = []

    def records():
        consumed.append("consumed")
        yield exportable_subject(
            "integration-id",
            birth_date="1991-02-03",
            country="Canada",
            visits=["2025-01-02"],
        )

    executed = execute_reuse_export(
        approved,
        records(),
        actor="alice",
        secret="integration-secret",
        now=datetime(2025, 1, 3, 11, 0, tzinfo=timezone.utc),
    )

    rows = [
        {
            "pseudonym": expected_pseudonym(
                "integration-id", secret="integration-secret"
            ),
            "country": "Canada",
            "birth_year": 1991,
            "visits": ["2025-01-02"],
        }
    ]
    canonical = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert executed["status"] == "executed"
    assert executed["execution_result"] == {
        "rows": rows,
        "manifest": {
            "algorithm": "sha256",
            "count": 1,
            "sha256": hashlib.sha256(canonical).hexdigest(),
        },
    }
    assert executed["history"] == [
        {"action": "submit", "actor": "alice", "at": "2025-01-01T09:00:00Z"},
        {"action": "approve", "actor": "bob", "at": "2025-01-02T10:00:00Z"},
        {"action": "execute", "actor": "alice", "at": "2025-01-03T11:00:00Z"},
    ]
    assert executed["privacy_plan"] == approved["privacy_plan"]
    assert executed["metadata"] == approved["metadata"]
    assert consumed == ["consumed"]
    assert draft == original_draft
    assert approved == original_approved


@pytest.mark.parametrize(
    ("local_now", "expected_count"),
    [
        (
            datetime(
                2025,
                1,
                1,
                23,
                30,
                tzinfo=timezone(timedelta(hours=-5)),
            ),
            1,
        ),
        (
            datetime(
                2025,
                1,
                2,
                0,
                30,
                tzinfo=timezone(timedelta(hours=14)),
            ),
            0,
        ),
    ],
)
def test_execution_uses_the_utc_date_for_consent_boundaries(
    local_now, expected_count
):
    value = exportable_subject(
        consents=[{"purpose": "research", "signed_on": "2025-01-02"}]
    )

    executed = execute_reuse_export(
        approved_request(),
        [value],
        actor="alice",
        secret="top-secret",
        now=local_now,
    )

    result = executed["execution_result"]
    assert result["manifest"]["count"] == expected_count
    if expected_count:
        assert result["rows"][0]["pseudonym"] == expected_pseudonym("id-one")
    else:
        assert result == {
            "rows": [],
            "manifest": {
                "algorithm": "sha256",
                "count": 0,
                "sha256": hashlib.sha256(b"[]").hexdigest(),
            },
        }


@pytest.mark.parametrize(
    ("status", "actor", "now", "exception_type", "code"),
    [
        ("draft", "alice", EXECUTION_NOW, ValueError, "INVALID_STATE"),
        ("approved", "intruder", EXECUTION_NOW, PermissionError, "REQUESTER_REQUIRED"),
        (
            "approved",
            "alice",
            datetime(2025, 2, 16, tzinfo=timezone.utc),
            ValueError,
            "APPROVAL_EXPIRED",
        ),
    ],
)
def test_governance_rejection_happens_before_subjects_are_consumed(
    status, actor, now, exception_type, code
):
    request = approved_request() if status == "approved" else reuse_request(status)
    original = deepcopy(request)
    consumed = []

    def records():
        consumed.append("consumed")
        yield exportable_subject()

    with pytest.raises(exception_type) as caught:
        execute_reuse_export(
            request,
            records(),
            actor=actor,
            secret="top-secret",
            now=now,
        )

    assert str(caught.value) == code
    assert consumed == []
    assert request == original


def test_export_failure_is_atomic_and_does_not_expose_partial_results():
    approved = approved_request(
        history=[{"action": "approve", "actor": "bob", "at": "2025-01-01T00:00:00Z"}]
    )
    original = deepcopy(approved)
    consumed = []
    values = [
        exportable_subject("first-private-id"),
        exportable_subject("second-private-id", birth_date="2025-02-30"),
    ]

    def records():
        for value in values:
            consumed.append(value["id"])
            yield value

    with pytest.raises(ValueError) as caught:
        execute_reuse_export(
            approved,
            records(),
            actor="alice",
            secret="failure-secret-913",
            now=EXECUTION_NOW,
        )

    assert str(caught.value) == "EXECUTION_FAILED"
    assert consumed == ["first-private-id", "second-private-id"]
    assert approved == original
    assert "execution_result" not in approved
    for private_value in (
        "first-private-id",
        "second-private-id",
        "Example Name",
        "private@example.test",
        "failure-secret-913",
    ):
        assert private_value not in str(caught.value)


def test_repeated_execution_is_rejected_without_consuming_or_changing_result():
    executed = execute_reuse_export(
        approved_request(),
        [exportable_subject()],
        actor="alice",
        secret="top-secret",
        now=EXECUTION_NOW,
    )
    original = deepcopy(executed)
    consumed = []

    def records():
        consumed.append("consumed")
        yield exportable_subject("another-id")

    with pytest.raises(ValueError) as caught:
        execute_reuse_export(
            executed,
            records(),
            actor="alice",
            secret="second-secret",
            now=EXECUTION_NOW + timedelta(days=1),
        )

    assert str(caught.value) == "INVALID_STATE"
    assert consumed == []
    assert executed == original


def test_success_and_failure_do_not_disclose_identifiers_or_secret():
    secret = "secret-do-not-disclose-837"
    direct_values = (
        "subject-direct-837",
        "Jane Direct 837",
        "jane.direct.837@example.test",
        "1987-04-06",
    )
    value = exportable_subject(
        direct_values[0],
        name=direct_values[1],
        email=direct_values[2],
        birth_date=direct_values[3],
    )

    executed = execute_reuse_export(
        approved_request(),
        [value],
        actor="alice",
        secret=secret,
        now=EXECUTION_NOW,
    )

    serialized = json.dumps(executed, ensure_ascii=False)
    for private_value in (*direct_values, secret):
        assert private_value not in serialized
    assert set(executed["execution_result"]["rows"][0]) == {
        "pseudonym",
        "country",
        "birth_year",
        "visits",
    }

    failed = exportable_subject(
        direct_values[0],
        name=direct_values[1],
        email=direct_values[2],
        birth_date=direct_values[3],
        visits=["not-a-date"],
    )
    with pytest.raises(ValueError) as caught:
        execute_reuse_export(
            approved_request(),
            [failed],
            actor="alice",
            secret=secret,
            now=EXECUTION_NOW,
        )
    assert str(caught.value) == "EXECUTION_FAILED"
    for private_value in (*direct_values, secret):
        assert private_value not in str(caught.value)
