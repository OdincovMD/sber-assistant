"""Idempotency helpers for inbound bank-message webhooks."""

import hashlib
import unicodedata
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def build_webhook_idempotency_key(
    sms_text: str,
    *,
    provided_key: Optional[str] = None,
    event_date: Optional[date] = None,
) -> str:
    """
    Build a privacy-safe key for duplicate webhook detection.

    A caller-provided key has priority. Legacy clients without one receive a
    deterministic daily fingerprint of normalized SMS text, allowing the same
    bank message text on different days while suppressing immediate retries.
    """
    normalized_provided = (provided_key or "").strip()
    if normalized_provided:
        material = f"provided:{normalized_provided}"
    else:
        bucket = event_date or datetime.now(MOSCOW_TZ).date()
        normalized_text = " ".join(
            unicodedata.normalize("NFKC", sms_text).split()
        )
        material = f"sms:{bucket.isoformat()}:{normalized_text}"

    return hashlib.sha256(material.encode("utf-8")).hexdigest()
