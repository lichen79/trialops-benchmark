"""A small tamper-evident chain for synthetic operational events."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

GENESIS_HASH = "0" * 64

_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP_PATTERN = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})Z\Z"
)
_REQUIRED_FIELDS = frozenset(
    {"sequence", "event_id", "actor", "action", "at", "payload", "previous_hash"}
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH_PATTERN.fullmatch(value) is not None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = _TIMESTAMP_PATTERN.fullmatch(value)
    if match is None:
        return None
    try:
        year, month, day, hour, minute, second = (
            int(part) for part in match.groups()
        )
        return datetime(
            year, month, day, hour, minute, second, tzinfo=timezone.utc
        )
    except (ValueError, OverflowError):
        return None


def _utc_timestamp(value: Any) -> str:
    if not isinstance(value, datetime):
        raise ValueError("at must be a timezone-aware datetime")
    try:
        offset = value.utcoffset()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("at must be a timezone-aware datetime") from None
    if value.tzinfo is None or offset is None:
        raise ValueError("at must be a timezone-aware datetime")
    if value.microsecond:
        raise ValueError("at must not contain microseconds")
    try:
        normalized = value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("at cannot be normalized to UTC") from None
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


class AuditChain:
    """Maintain an append-only sequence whose record hashes are linkable."""

    def __init__(self) -> None:
        self.previous_hash = GENESIS_HASH
        self.records: list[dict[str, Any]] = []
        self._event_ids: set[str] = set()


    def head(self) -> str:
        """Return the current trusted head, or the genesis hash when empty."""

        return self.previous_hash

    def append(
        self,
        *,
        event_id: str,
        actor: str,
        action: str,
        at: datetime,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Append and return one canonical, hashed audit record."""

        event_id = _nonempty_string(event_id, "event_id")
        actor = _nonempty_string(actor, "actor")
        action = _nonempty_string(action, "action")
        if event_id in self._event_ids:
            raise ValueError("event_id must be unique")
        timestamp = _utc_timestamp(at)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")

        try:
            record: dict[str, Any] = {
                "sequence": len(self.records) + 1,
                "event_id": event_id,
                "actor": actor,
                "action": action,
                "at": timestamp,
                "payload": deepcopy(payload),
                "previous_hash": self.previous_hash,
            }
            record["hash"] = hashlib.sha256(
                _canonical(record).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
            raise ValueError("payload must be JSON serializable") from None

        self.records.append(record)
        self._event_ids.add(event_id)
        self.previous_hash = record["hash"]
        return record

    @staticmethod
    def verify(
        records: Any,
        *,
        expected_head: str | None = None,
        expected_length: int | None = None,
    ) -> list[str]:
        """Verify internal invariants and optional trusted head and length."""

        try:
            records = list(records)
        except (TypeError, ValueError):
            return ["MALFORMED_RECORD"]

        errors: set[str] = set()
        if expected_length is not None and (
            type(expected_length) is not int or expected_length != len(records)
        ):
            errors.add("LENGTH_MISMATCH")
        if expected_head is not None:
            if not records:
                actual_head = GENESIS_HASH
            elif isinstance(records[-1], dict):
                actual_head = records[-1].get("hash")
            else:
                actual_head = None
            if expected_head != actual_head:
                errors.add("HEAD_MISMATCH")
        seen_event_ids: set[str] = set()
        previous_record: Any = None
        previous_timestamp: datetime | None = None

        for expected_sequence, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                errors.add("MALFORMED_RECORD")
                previous_record = None
                continue

            has_required_fields = _REQUIRED_FIELDS.issubset(record)
            if not has_required_fields:
                errors.add("MALFORMED_RECORD")

            if type(record.get("sequence")) is not int or record.get("sequence") != expected_sequence:
                errors.add("BAD_SEQUENCE")

            event_id = record.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                errors.add("MALFORMED_RECORD")
            elif event_id in seen_event_ids:
                errors.add("DUPLICATE_EVENT_ID")
            else:
                seen_event_ids.add(event_id)

            for field in ("actor", "action"):
                value = record.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.add("MALFORMED_RECORD")

            if not isinstance(record.get("payload"), dict):
                errors.add("BAD_PAYLOAD")

            timestamp = _parse_timestamp(record.get("at"))
            if timestamp is None:
                errors.add("MALFORMED_TIMESTAMP")
            else:
                if previous_timestamp is not None and timestamp <= previous_timestamp:
                    errors.add("NON_INCREASING_TIMESTAMP")
                previous_timestamp = timestamp

            current_hash_valid = _valid_hash(record.get("hash"))
            previous_hash_valid = _valid_hash(record.get("previous_hash"))
            if not current_hash_valid or not previous_hash_valid:
                errors.add("MALFORMED_HASH")

            if previous_hash_valid:
                if expected_sequence == 1:
                    expected_previous_hash = GENESIS_HASH
                elif isinstance(previous_record, dict) and _valid_hash(
                    previous_record.get("hash")
                ):
                    expected_previous_hash = previous_record["hash"]
                else:
                    expected_previous_hash = None
                if (
                    expected_previous_hash is not None
                    and record["previous_hash"] != expected_previous_hash
                ):
                    errors.add("BAD_PREVIOUS_HASH")

            if current_hash_valid and has_required_fields:
                unhashed_record = {
                    key: value for key, value in record.items() if key != "hash"
                }
                try:
                    expected_hash = hashlib.sha256(
                        _canonical(unhashed_record).encode("utf-8")
                    ).hexdigest()
                except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
                    errors.add("BAD_PAYLOAD")
                else:
                    if record["hash"] != expected_hash:
                        errors.add("BAD_HASH")

            previous_record = record

        return sorted(errors)
