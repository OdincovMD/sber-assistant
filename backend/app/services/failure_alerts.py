"""Pure formatting rules for terminal transaction failure notifications."""

from datetime import datetime
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
MAX_ERROR_LENGTH = 500


def build_transaction_failure_alert(
    *,
    transaction_id: int,
    created_at: datetime,
    error: str | None,
    recovery_attempts: int,
) -> str:
    """Build a safe operator alert without including the original SMS."""
    received_at = created_at
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=MOSCOW_TZ)
    received_at = received_at.astimezone(MOSCOW_TZ)

    normalized_error = " ".join((error or "Причина не указана").split())
    if len(normalized_error) > MAX_ERROR_LENGTH:
        normalized_error = normalized_error[: MAX_ERROR_LENGTH - 1] + "…"

    return "\n".join(
        [
            "ОШИБКА ОБРАБОТКИ ОПЕРАЦИИ",
            f"ID: {transaction_id}",
            f"Получена: {received_at:%d.%m.%Y %H:%M} МСК",
            f"Автовосстановлений: {recovery_attempts}",
            f"Причина: {normalized_error}",
            "",
            "После устранения причины повторите обработку:",
            f"POST /api/transactions/{transaction_id}/retry",
        ]
    )
