from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from trialops.governance import apply_reuse_action


NOW = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)


def request(status="draft", **changes):
    purpose = changes.get("purpose", "synthetic methods research")
    value = {
        "id": "reuse-001",
        "requester": "alice",
        "reviewer": "bob",
        "purpose": purpose,
        "status": status,
        "retention_until": "2025-06-30",
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


def successful_executor(approved_request):
    return {"receipt": "synthetic-execution-001"}


def test_full_workflow_normalizes_history_and_deep_copies():
    original = request(history=[{"action": "imported", "nested": {"value": 1}}])
    snapshot = deepcopy(original)
    executor_calls = []

    def executor(approved_request):
        executor_calls.append(approved_request)
        approved_request["metadata"]["quality_score"] = 0
        return {"rows": 3}

    submitted = apply_reuse_action(original, "submit", "alice", NOW)
    assert original == snapshot
    assert submitted is not original
    assert submitted["metadata"] is not original["metadata"]
    assert submitted["history"][0] is not original["history"][0]
    assert submitted["history"][-1] == {
        "action": "submit",
        "actor": "alice",
        "at": "2025-01-01T12:00:00Z",
    }

    approved = apply_reuse_action(submitted, "approve", "bob", NOW + timedelta(days=1))
    assert approved["approval_expires_on"] == "2025-02-01"
    executed = apply_reuse_action(
        approved, "execute", "alice", NOW + timedelta(days=2), executor
    )
    assert executed["status"] == "executed"
    assert executed["execution_result"] == {"rows": 3}
    assert len(executor_calls) == 1
    assert executor_calls[0]["status"] == "approved"
    assert approved["status"] == "approved"
    assert approved["metadata"]["quality_score"] == 0.95
    assert "execution_result" not in approved
    assert [entry["action"] for entry in executed["history"][-3:]] == [
        "submit",
        "approve",
        "execute",
    ]
    assert [entry["at"] for entry in executed["history"][-3:]] == [
        "2025-01-01T12:00:00Z",
        "2025-01-02T12:00:00Z",
        "2025-01-03T12:00:00Z",
    ]


@pytest.mark.parametrize(
    ("status", "action", "actor"),
    [
        ("draft", "submit", "bob"),
        ("submitted", "approve", "alice"),
        ("submitted", "reject", "alice"),
        ("approved", "execute", "bob"),
    ],
)
def test_invalid_roles_raise_permission_error(status, action, actor):
    candidate = request(status, approval_expires_on="2025-02-01")
    with pytest.raises(PermissionError):
        apply_reuse_action(candidate, action, actor, NOW, successful_executor)


def test_approval_enforces_separation_exact_privacy_plan_and_metadata():
    with pytest.raises(ValueError, match="SELF_REVIEW"):
        apply_reuse_action(request("submitted", reviewer="alice"), "approve", "alice", NOW)

    wrong_plan = request("submitted")
    wrong_plan["privacy_plan"]["algorithm"] = "hmac-sha1"
    with pytest.raises(ValueError, match="PSEUDONYMIZATION_REQUIRED"):
        apply_reuse_action(wrong_plan, "approve", "bob", NOW)

    incomplete = request("submitted")
    incomplete["metadata"]["complete"] = False
    with pytest.raises(ValueError, match="METADATA_REQUIRED"):
        apply_reuse_action(incomplete, "approve", "bob", NOW)

    low_quality = request("submitted")
    low_quality["metadata"]["quality_score"] = 0.949
    with pytest.raises(ValueError, match="METADATA_REQUIRED"):
        apply_reuse_action(low_quality, "approve", "bob", NOW)

    with pytest.raises(ValueError, match="METADATA_REQUIRED"):
        apply_reuse_action(request("submitted", purpose="  "), "approve", "bob", NOW)


def test_approval_and_execution_respect_retention_and_stale_approvals():
    expired_retention = request("submitted", retention_until="2024-12-31")
    with pytest.raises(ValueError, match="RETENTION_EXPIRED"):
        apply_reuse_action(expired_retention, "approve", "bob", NOW)

    stale = request("approved", approval_expires_on="2024-12-31")
    with pytest.raises(ValueError, match="APPROVAL_EXPIRED"):
        apply_reuse_action(stale, "execute", "alice", NOW, successful_executor)

    current = request("approved", approval_expires_on="2025-01-01")
    assert apply_reuse_action(
        current, "execute", "alice", NOW, successful_executor
    )["status"] == "executed"


def test_execute_requires_a_callable_and_wraps_failures_atomically():
    approved = request("approved", approval_expires_on="2025-02-01")
    with pytest.raises(ValueError, match="EXECUTOR_REQUIRED"):
        apply_reuse_action(approved, "execute", "alice", NOW)

    snapshot = deepcopy(approved)
    calls = []

    def failing_executor(executor_input):
        calls.append(executor_input)
        executor_input["status"] = "executed"
        executor_input["history"].append({"action": "fake"})
        raise RuntimeError("synthetic backend failure")

    with pytest.raises(ValueError, match="EXECUTION_FAILED"):
        apply_reuse_action(approved, "execute", "alice", NOW, failing_executor)
    assert len(calls) == 1
    assert approved == snapshot
    assert approved["status"] == "approved"
    assert approved["history"] == []


def test_expire_is_strict_and_appends_history_for_any_actor():
    approved = request("approved", approval_expires_on="2025-01-01")
    with pytest.raises(ValueError, match="INVALID_STATE"):
        apply_reuse_action(approved, "expire", "system", NOW)
    expired = apply_reuse_action(
        approved, "expire", "system", NOW + timedelta(days=1)
    )
    assert expired["status"] == "expired"
    assert expired["history"][-1]["actor"] == "system"

    submitted = request("submitted", retention_until="2025-01-01")
    assert apply_reuse_action(
        submitted, "expire", "anyone", NOW + timedelta(days=1)
    )["status"] == "expired"


def test_invalid_states_and_naive_now_are_rejected():
    with pytest.raises(ValueError, match="INVALID_STATE"):
        apply_reuse_action(request("approved"), "submit", "alice", NOW)
    with pytest.raises(ValueError, match="INVALID_NOW"):
        apply_reuse_action(request(), "submit", "alice", datetime(2025, 1, 1))
