from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from trialops.governance import apply_reuse_action


NOW = datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc)


def request(status="draft", **changes):
    purpose = changes.get("purpose", "synthetic reuse")
    value = {
        "id": "request-1",
        "requester": "requester",
        "reviewer": "reviewer",
        "purpose": purpose,
        "status": status,
        "retention_until": "2025-12-31",
        "privacy_plan": {
            "algorithm": "hmac-sha256",
            "domain": purpose,
            "fields": ["pseudonym", "country", "birth_year", "visits"],
        },
        "metadata": {
            "representative": True,
            "complete": True,
            "quality_score": 0.95,
        },
        "history": [],
    }
    value.update(changes)
    return value


class Recorder:
    def __init__(self, result=None):
        self.calls = []
        self.result = {"receipt": "synthetic"} if result is None else result

    def __call__(self, approved_request):
        self.calls.append(approved_request)
        return self.result


def test_approval_expiry_and_history_use_the_utc_instant():
    local_now = datetime(
        2025,
        1,
        1,
        23,
        30,
        0,
        123456,
        tzinfo=timezone(timedelta(hours=-5)),
    )
    result = apply_reuse_action(request("submitted"), "approve", "reviewer", local_now)
    assert result["approval_expires_on"] == "2025-02-01"
    assert result["history"] == [
        {
            "action": "approve",
            "actor": "reviewer",
            "at": "2025-01-02T04:30:00.123456Z",
        }
    ]


def test_retention_boundary_is_inclusive_on_the_utc_date():
    local_now = datetime(2025, 1, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    allowed = request("submitted", retention_until="2025-01-02")
    assert apply_reuse_action(allowed, "approve", "reviewer", local_now)["status"] == "approved"
    expired = request("submitted", retention_until="2025-01-01")
    with pytest.raises(ValueError, match="RETENTION_EXPIRED"):
        apply_reuse_action(expired, "approve", "reviewer", local_now)


def test_nested_request_data_is_not_shared_with_the_result():
    original = request(history=[{"action": "old", "detail": {"items": [1]}}])
    snapshot = deepcopy(original)
    result = apply_reuse_action(original, "submit", "requester", NOW)
    result["metadata"]["quality_score"] = 1
    result["history"][0]["detail"]["items"].append(2)
    assert original == snapshot

    with pytest.raises(PermissionError):
        apply_reuse_action(original, "submit", "intruder", NOW)
    assert original == snapshot


@pytest.mark.parametrize(
    ("action", "actor", "status"),
    [
        ("submit", "reviewer", "draft"),
        ("approve", "requester", "submitted"),
        ("reject", "requester", "submitted"),
        ("execute", "reviewer", "approved"),
    ],
)
def test_each_transition_has_the_required_role(action, actor, status):
    candidate = request(status, approval_expires_on="2025-02-01")
    recorder = Recorder()
    with pytest.raises(PermissionError):
        apply_reuse_action(candidate, action, actor, NOW, recorder)
    assert recorder.calls == []


@pytest.mark.parametrize(
    ("action", "actor", "status"),
    [
        ("submit", "requester", "submitted"),
        ("approve", "reviewer", "draft"),
        ("reject", "reviewer", "approved"),
        ("execute", "requester", "submitted"),
        ("expire", "anyone", "draft"),
        ("expire", "anyone", "executed"),
    ],
)
def test_invalid_states_have_a_stable_error_and_no_executor_call(action, actor, status):
    recorder = Recorder()
    with pytest.raises(ValueError, match="INVALID_STATE"):
        apply_reuse_action(request(status), action, actor, NOW, recorder)
    assert recorder.calls == []


def test_self_review_is_rejected_before_approval():
    candidate = request("submitted", reviewer="requester")
    with pytest.raises(ValueError, match="SELF_REVIEW"):
        apply_reuse_action(candidate, "approve", "requester", NOW)


@pytest.mark.parametrize(
    "plan",
    [
        None,
        True,
        {},
        {
            "algorithm": "hmac-sha1",
            "domain": "synthetic reuse",
            "fields": ["pseudonym", "country", "birth_year", "visits"],
        },
        {
            "algorithm": "hmac-sha256",
            "domain": "another-purpose",
            "fields": ["pseudonym", "country", "birth_year", "visits"],
        },
        {
            "algorithm": "hmac-sha256",
            "domain": "synthetic reuse",
            "fields": ["country", "pseudonym", "birth_year", "visits"],
        },
        {
            "algorithm": "hmac-sha256",
            "domain": "synthetic reuse",
            "fields": ["pseudonym", "country", "birth_year", "visits"],
            "extra": True,
        },
    ],
)
def test_privacy_plan_must_match_exactly(plan):
    candidate = request("submitted", privacy_plan=plan, pseudonymized=True)
    with pytest.raises(ValueError, match="PSEUDONYMIZATION_REQUIRED"):
        apply_reuse_action(candidate, "approve", "reviewer", NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("representative", False),
        ("complete", False),
        ("quality_score", 0.9499),
        ("quality_score", True),
        ("quality_score", float("nan")),
        ("quality_score", float("inf")),
    ],
)
def test_metadata_guards_reject_missing_or_invalid_evidence(field, value):
    candidate = request("submitted")
    candidate["metadata"][field] = value
    with pytest.raises(ValueError, match="METADATA_REQUIRED"):
        apply_reuse_action(candidate, "approve", "reviewer", NOW)


@pytest.mark.parametrize("purpose", ["", "   ", None])
def test_nonempty_purpose_is_part_of_the_metadata_guard(purpose):
    with pytest.raises(ValueError, match="METADATA_REQUIRED"):
        apply_reuse_action(
            request("submitted", purpose=purpose), "approve", "reviewer", NOW
        )


def test_execute_checks_all_preconditions_before_calling_executor():
    recorder = Recorder()
    stale = request("approved", approval_expires_on="2025-01-09")
    with pytest.raises(ValueError, match="APPROVAL_EXPIRED"):
        apply_reuse_action(stale, "execute", "requester", NOW, recorder)

    missing_expiry = request("approved")
    with pytest.raises(ValueError, match="APPROVAL_EXPIRED"):
        apply_reuse_action(missing_expiry, "execute", "requester", NOW, recorder)

    retention_expired = request(
        "approved", approval_expires_on="2025-01-20", retention_until="2025-01-09"
    )
    with pytest.raises(ValueError, match="RETENTION_EXPIRED"):
        apply_reuse_action(retention_expired, "execute", "requester", NOW, recorder)

    wrong_plan = request(
        "approved", approval_expires_on="2025-01-20", privacy_plan=None
    )
    with pytest.raises(ValueError, match="PSEUDONYMIZATION_REQUIRED"):
        apply_reuse_action(wrong_plan, "execute", "requester", NOW, recorder)
    assert recorder.calls == []


@pytest.mark.parametrize("executor", [None, False, 0, "not-callable"])
def test_execute_requires_a_callable_executor(executor):
    candidate = request("approved", approval_expires_on="2025-02-01")
    with pytest.raises(ValueError, match="EXECUTOR_REQUIRED"):
        apply_reuse_action(candidate, "execute", "requester", NOW, executor)


def test_execution_is_allowed_through_expiry_and_retention_dates():
    candidate = request(
        "approved", approval_expires_on="2025-01-10", retention_until="2025-01-10"
    )
    recorder = Recorder({"export_id": "synthetic-1"})
    result = apply_reuse_action(candidate, "execute", "requester", NOW, recorder)
    assert result["status"] == "executed"
    assert result["execution_result"] == {"export_id": "synthetic-1"}
    assert len(recorder.calls) == 1


def test_executor_receives_one_deep_copied_approved_snapshot():
    candidate = request(
        "approved",
        approval_expires_on="2025-02-01",
        history=[{"action": "approve", "nested": {"items": [1]}}],
    )
    snapshot = deepcopy(candidate)
    recorder = Recorder()
    result = apply_reuse_action(candidate, "execute", "requester", NOW, recorder)

    assert len(recorder.calls) == 1
    executor_input = recorder.calls[0]
    assert executor_input["status"] == "approved"
    assert "execution_result" not in executor_input
    assert executor_input is not candidate
    assert executor_input["metadata"] is not candidate["metadata"]
    assert executor_input["history"][0] is not candidate["history"][0]
    executor_input["history"][0]["nested"]["items"].append(2)
    assert candidate == snapshot
    assert result["history"][-1]["action"] == "execute"


def test_executor_exception_is_atomic_and_never_reports_executed():
    candidate = request("approved", approval_expires_on="2025-02-01")
    snapshot = deepcopy(candidate)
    calls = []

    def fail(executor_input):
        calls.append(executor_input)
        executor_input["status"] = "executed"
        executor_input["history"].append({"action": "forged"})
        raise RuntimeError("backend unavailable")

    with pytest.raises(ValueError, match="EXECUTION_FAILED"):
        apply_reuse_action(candidate, "execute", "requester", NOW, fail)
    assert len(calls) == 1
    assert candidate == snapshot
    assert candidate["status"] == "approved"
    assert candidate["history"] == []


def test_non_dict_executor_result_is_an_atomic_execution_failure():
    candidate = request("approved", approval_expires_on="2025-02-01")
    snapshot = deepcopy(candidate)
    recorder = Recorder(result=["not", "a", "dict"])
    with pytest.raises(ValueError, match="EXECUTION_FAILED"):
        apply_reuse_action(candidate, "execute", "requester", NOW, recorder)
    assert len(recorder.calls) == 1
    assert candidate == snapshot


def test_repeated_execution_does_not_call_executor_again():
    recorder = Recorder()
    first = apply_reuse_action(
        request("approved", approval_expires_on="2025-02-01"),
        "execute",
        "requester",
        NOW,
        recorder,
    )
    with pytest.raises(ValueError, match="INVALID_STATE"):
        apply_reuse_action(first, "execute", "requester", NOW, recorder)
    assert len(recorder.calls) == 1


def test_non_execute_actions_never_call_the_executor():
    recorder = Recorder()
    approved = apply_reuse_action(
        request("submitted"), "approve", "reviewer", NOW, recorder
    )
    assert approved["status"] == "approved"
    assert "execution_result" not in approved
    assert recorder.calls == []


def test_expire_uses_the_current_state_field_and_strict_comparison():
    approved = request("approved", approval_expires_on="2025-01-10")
    with pytest.raises(ValueError, match="INVALID_STATE"):
        apply_reuse_action(approved, "expire", "nobody-in-particular", NOW)
    result = apply_reuse_action(
        approved, "expire", "nobody-in-particular", NOW + timedelta(days=1)
    )
    assert result["status"] == "expired"
    assert result["history"][-1]["action"] == "expire"

    submitted = request("submitted", retention_until="2025-01-10")
    with pytest.raises(ValueError, match="INVALID_STATE"):
        apply_reuse_action(submitted, "expire", "system", NOW)
    assert apply_reuse_action(
        submitted, "expire", "system", NOW + timedelta(days=1)
    )["status"] == "expired"


def test_submit_and_reject_do_not_apply_approval_policy_checks():
    draft = request(privacy_plan=None, retention_until="2024-01-01", purpose="")
    assert apply_reuse_action(draft, "submit", "requester", NOW)["status"] == "submitted"

    submitted = request(
        "submitted", privacy_plan=None, retention_until="2024-01-01", purpose=""
    )
    rejected = apply_reuse_action(submitted, "reject", "reviewer", NOW)
    assert rejected["status"] == "rejected"
    assert rejected["history"][-1] == {
        "action": "reject",
        "actor": "reviewer",
        "at": "2025-01-10T12:00:00Z",
    }


def test_history_is_appended_not_reordered_or_rebuilt():
    existing = [
        {"action": "later", "actor": "old", "at": "2026-01-01T00:00:00Z"},
        {"action": "earlier", "actor": "old", "at": "2024-01-01T00:00:00Z"},
    ]
    result = apply_reuse_action(request(history=existing), "submit", "requester", NOW)
    assert result["history"][:2] == existing
    assert result["history"][-1]["at"] == "2025-01-10T12:00:00Z"


def test_unknown_action_and_naive_now_are_rejected_without_mutation():
    candidate = request()
    snapshot = deepcopy(candidate)
    recorder = Recorder()
    with pytest.raises(ValueError, match="INVALID_ACTION"):
        apply_reuse_action(candidate, "undo", "requester", NOW, recorder)
    with pytest.raises(ValueError, match="INVALID_NOW"):
        apply_reuse_action(candidate, "submit", "requester", datetime(2025, 1, 10), recorder)
    assert candidate == snapshot
    assert recorder.calls == []
