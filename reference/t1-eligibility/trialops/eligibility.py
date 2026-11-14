"""Deterministic eligibility checks for synthetic screening data."""

from datetime import date, datetime
import math
from collections.abc import Mapping
from typing import Any


def _value(mapping: Mapping[str, Any], key: str, prefix: str) -> Any:
    try:
        return mapping[key]
    except (KeyError, TypeError):
        raise ValueError(f"missing required field {prefix}.{key}") from None


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field} must be an ISO date") from None


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    for item in value:
        _string(item, field)
    return value


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def _age(birth_date: date, on: date) -> int:
    return on.year - birth_date.year - (
        (on.month, on.day) < (birth_date.month, birth_date.day)
    )


def evaluate_subject(
    subject: Mapping[str, Any], protocol: Mapping[str, Any], on: date
) -> dict[str, Any]:
    """Evaluate one fictional subject against a synthetic protocol.

    The most recent consent signed on or before ``on`` is selected, including
    when that consent is retired or has the wrong version. All returned reason
    codes follow the protocol-defined ordering.
    """

    subject = _mapping(subject, "subject")
    protocol = _mapping(protocol, "protocol")
    if not isinstance(on, date) or isinstance(on, datetime):
        raise ValueError("on must be a date")

    birth_date = _iso_date(_value(subject, "birth_date", "subject"), "subject.birth_date")
    consents = _value(subject, "consents", "subject")
    subject_labs = _mapping(_value(subject, "labs", "subject"), "subject.labs")
    flags = set(
        _string_list(_value(subject, "flags", "subject"), "subject.flags")
    )

    min_age = _value(protocol, "min_age", "protocol")
    max_age = _value(protocol, "max_age", "protocol")
    for value, field in ((min_age, "protocol.min_age"), (max_age, "protocol.max_age")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a nonnegative integer")
    if min_age > max_age:
        raise ValueError("protocol.min_age must not exceed protocol.max_age")

    required_version = _string(
        _value(protocol, "required_consent_version", "protocol"),
        "protocol.required_consent_version",
    )
    consent_valid_from = _iso_date(
        _value(protocol, "consent_valid_from", "protocol"),
        "protocol.consent_valid_from",
    )
    protocol_labs = _mapping(
        _value(protocol, "labs", "protocol"), "protocol.labs"
    )
    exclusions = _string_list(
        _value(protocol, "exclusions", "protocol"), "protocol.exclusions"
    )

    if not isinstance(consents, list):
        raise ValueError("subject.consents must be a list")
    parsed_consents: list[tuple[date, str]] = []
    for consent in consents:
        consent = _mapping(consent, "subject.consents entry")
        version = _string(
            _value(consent, "version", "subject.consents entry"),
            "subject.consents.version",
        )
        signed_on = _iso_date(
            _value(consent, "signed_on", "subject.consents entry"),
            "subject.consents.signed_on",
        )
        parsed_consents.append((signed_on, version))

    for name, value in subject_labs.items():
        _string(name, "subject.labs name")
        _number(value, f"subject.labs.{name}")

    lab_ranges: dict[str, tuple[int | float, int | float]] = {}
    for name, bounds in protocol_labs.items():
        _string(name, "protocol.labs name")
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError(f"protocol.labs.{name} must be [low, high]")
        low = _number(bounds[0], f"protocol.labs.{name} low")
        high = _number(bounds[1], f"protocol.labs.{name} high")
        if low > high:
            raise ValueError(f"protocol.labs.{name} low must not exceed high")
        if name not in subject_labs:
            raise ValueError(f"missing required field subject.labs.{name}")
        lab_ranges[name] = (low, high)

    selected: tuple[date, str] | None = None
    for signed_on, version in parsed_consents:
        if signed_on <= on and (selected is None or signed_on >= selected[0]):
            selected = (signed_on, version)

    reasons: list[str] = []
    if selected is None or selected[0] < consent_valid_from:
        reasons.append("CONSENT_MISSING")
    if selected is not None and selected[1] != required_version:
        reasons.append("CONSENT_VERSION")

    age = _age(birth_date, on)
    if age < min_age:
        reasons.append("AGE_BELOW_MIN")
    if age > max_age:
        reasons.append("AGE_ABOVE_MAX")

    for flag in exclusions:
        if flag in flags:
            reasons.append(f"EXCLUSION_{flag}")

    for name in sorted(lab_ranges):
        low, high = lab_ranges[name]
        value = subject_labs[name]
        if value < low:
            reasons.append(f"LAB_{name}_LOW")
        elif value > high:
            reasons.append(f"LAB_{name}_HIGH")

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "consent_version": selected[1] if selected is not None else None,
    }
