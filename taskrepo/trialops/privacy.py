"""Purpose-scoped, pseudonymized exports of fictional subject records."""

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any
import hashlib
import hmac
import json
import re


_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


def _field(record: Mapping[str, Any], name: str) -> Any:
    try:
        return record[name]
    except (KeyError, TypeError):
        raise ValueError(f"missing required field {name}") from None


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field} must be an ISO date") from None


def _text(value: Any, field: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        qualifier = "a non-empty" if nonempty else "a"
        raise ValueError(f"{field} must be {qualifier} string")
    return value


def export_subjects(
    subjects: Iterable[Mapping[str, Any]], *, purpose: str, secret: str, on: date
) -> dict[str, Any]:
    """Export only active, unpurged records without direct identifiers."""

    purpose = _text(purpose, "purpose", nonempty=True)
    secret = _text(secret, "secret", nonempty=True)
    if not isinstance(on, date) or isinstance(on, datetime):
        raise ValueError("on must be a date")
    if isinstance(subjects, (str, bytes, Mapping)) or not isinstance(
        subjects, Iterable
    ):
        raise ValueError("subjects must be an iterable of records")

    try:
        secret_bytes = secret.encode("utf-8")
        purpose.encode("utf-8")
    except UnicodeError:
        raise ValueError("purpose and secret must contain valid Unicode") from None

    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for subject in subjects:
        if not isinstance(subject, Mapping):
            raise ValueError("each subject must be a mapping")

        subject_id = _text(_field(subject, "id"), "id", nonempty=True)
        if _ID_PATTERN.fullmatch(subject_id) is None:
            raise ValueError("id must match [A-Za-z0-9_-]+")
        if subject_id in seen_ids:
            raise ValueError("DUPLICATE_SUBJECT_ID")
        seen_ids.add(subject_id)

        _text(_field(subject, "name"), "name")
        birth_date = _date(_field(subject, "birth_date"), "birth_date")
        _text(_field(subject, "email"), "email")
        country = _text(_field(subject, "country"), "country")

        visits_value = _field(subject, "visits")
        if not isinstance(visits_value, list):
            raise ValueError("visits must be a list")
        visits = sorted(
            (_date(value, "visits") for value in visits_value),
            key=lambda value: value,
        )

        consents_value = _field(subject, "consents")
        if not isinstance(consents_value, list):
            raise ValueError("consents must be a list")
        parsed_consents: list[tuple[str, date, date | None]] = []
        for consent in consents_value:
            if not isinstance(consent, Mapping):
                raise ValueError("each consent must be a mapping")
            consent_purpose = _text(_field(consent, "purpose"), "consent purpose")
            signed_on = _date(_field(consent, "signed_on"), "consent signed_on")
            withdrawn_value = consent.get("withdrawn_on")
            withdrawn_on = (
                None
                if withdrawn_value is None
                else _date(withdrawn_value, "consent withdrawn_on")
            )
            parsed_consents.append((consent_purpose, signed_on, withdrawn_on))

        purge_after = _date(_field(subject, "purge_after"), "purge_after")
        has_active_consent = any(
            consent_purpose == purpose
            and signed_on <= on
            and (withdrawn_on is None or withdrawn_on > on)
            for consent_purpose, signed_on, withdrawn_on in parsed_consents
        )
        if not has_active_consent or purge_after < on:
            continue

        message = f"{purpose}:{subject_id}".encode("utf-8")
        pseudonym = hmac.new(secret_bytes, message, hashlib.sha256).hexdigest()[:16]
        rows.append(
            {
                "pseudonym": pseudonym,
                "country": country,
                "birth_year": birth_date.year,
                "visits": [value.isoformat() for value in visits],
            }
        )

    rows.sort(key=lambda row: row["pseudonym"])
    try:
        canonical_rows = json.dumps(
            rows,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("export rows must contain valid JSON text") from None

    return {
        "rows": rows,
        "manifest": {
            "algorithm": "sha256",
            "count": len(rows),
            "sha256": hashlib.sha256(canonical_rows).hexdigest(),
        },
    }


