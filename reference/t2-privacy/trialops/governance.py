"""Synthetic data-reuse workflow with explicit, deterministic guardrails."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone

_ACTIONS = {"submit", "approve", "reject", "execute", "expire"}
_PRIVACY_FIELDS = ["pseudonym", "country", "birth_year", "visits"]


def _aware_utc(now: object) -> datetime:
    if not isinstance(now, datetime):
        raise ValueError("INVALID_NOW")
    try:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("INVALID_NOW")
        return now.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("INVALID_NOW") from exc


def _format_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _require_state(request: dict[str, object], expected: str | tuple[str, ...]) -> None:
    states = (expected,) if isinstance(expected, str) else expected
    if request.get("status") not in states:
        raise ValueError("INVALID_STATE")


def _require_actor(actor: object, expected: object, code: str) -> None:
    if actor != expected:
        raise PermissionError(code)


def _date_field(request: Mapping[str, object], field: str, error_code: str) -> date:
    value = request.get(field)
    if not isinstance(value, str):
        raise ValueError(error_code)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(error_code) from exc


def _valid_quality_score(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value >= 0.95


def _check_policy(request: Mapping[str, object], now_utc: datetime) -> None:
    purpose = request.get("purpose")
    expected_privacy_plan = {
        "algorithm": "hmac-sha256",
        "domain": purpose,
        "fields": _PRIVACY_FIELDS,
    }
    if request.get("privacy_plan") != expected_privacy_plan:
        raise ValueError("PSEUDONYMIZATION_REQUIRED")

    metadata = request.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("representative") is not True
        or metadata.get("complete") is not True
        or not _valid_quality_score(metadata.get("quality_score"))
        or not isinstance(purpose, str)
        or not purpose.strip()
    ):
        raise ValueError("METADATA_REQUIRED")

    retention_until = _date_field(request, "retention_until", "RETENTION_EXPIRED")
    if now_utc.date() > retention_until:
        raise ValueError("RETENTION_EXPIRED")


def _check_approval_current(request: Mapping[str, object], now_utc: datetime) -> None:
    expires_on = _date_field(request, "approval_expires_on", "APPROVAL_EXPIRED")
    if now_utc.date() > expires_on:
        raise ValueError("APPROVAL_EXPIRED")


def apply_reuse_action(
    request: dict[str, object],
    action: str,
    actor: str,
    now: datetime,
    executor=None,
) -> dict[str, object]:
    """Apply one action to a deep copy of a synthetic reuse request."""
    if not isinstance(request, dict):
        raise TypeError("request must be a dict")
    result = copy.deepcopy(request)
    if not isinstance(action, str) or action not in _ACTIONS:
        raise ValueError("INVALID_ACTION")
    now_utc = _aware_utc(now)
    history = result.get("history")
    if not isinstance(history, list):
        raise ValueError("INVALID_STATE")

    if action == "submit":
        _require_actor(actor, result.get("requester"), "REQUESTER_REQUIRED")
        _require_state(result, "draft")
        result["status"] = "submitted"

    elif action == "approve":
        _require_actor(actor, result.get("reviewer"), "REVIEWER_REQUIRED")
        _require_state(result, "submitted")
        if result.get("requester") == result.get("reviewer"):
            raise ValueError("SELF_REVIEW")
        _check_policy(result, now_utc)
        result["approval_expires_on"] = (
            now_utc.date() + timedelta(days=30)
        ).isoformat()
        result["status"] = "approved"

    elif action == "reject":
        _require_actor(actor, result.get("reviewer"), "REVIEWER_REQUIRED")
        _require_state(result, "submitted")
        result["status"] = "rejected"

    elif action == "execute":
        _require_actor(actor, result.get("requester"), "REQUESTER_REQUIRED")
        _require_state(result, "approved")
        _check_approval_current(result, now_utc)
        _check_policy(result, now_utc)
        if not callable(executor):
            raise ValueError("EXECUTOR_REQUIRED")
        try:
            # The executor sees an approved snapshot, never the caller's object
            # or the in-progress executed result.
            execution_result = executor(copy.deepcopy(result))
            if not isinstance(execution_result, dict):
                raise TypeError("executor must return a dict")
            execution_result = copy.deepcopy(execution_result)
        except Exception as exc:
            raise ValueError("EXECUTION_FAILED") from exc
        result["execution_result"] = execution_result
        result["status"] = "executed"

    else:  # expire
        _require_state(result, ("approved", "submitted"))
        expiry_field = (
            "approval_expires_on"
            if result.get("status") == "approved"
            else "retention_until"
        )
        expires_on = _date_field(result, expiry_field, "INVALID_STATE")
        if now_utc.date() <= expires_on:
            raise ValueError("INVALID_STATE")
        result["status"] = "expired"

    history.append(
        {
            "action": action,
            "actor": copy.deepcopy(actor),
            "at": _format_utc(now_utc),
        }
    )
    return result
