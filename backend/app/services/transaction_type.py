"""Deterministic transaction type normalization for parsed bank messages."""

from typing import Optional


SUPPORTED_TRANSACTION_TYPES = {
    "purchase",
    "payment",
    "transfer",
    "deposit",
    "withdrawal",
    "fee",
    "unknown",
}


def normalize_transaction_type(
    *,
    explicit_type: Optional[str],
    account_type: Optional[str],
    category: Optional[str],
    is_expense: Optional[bool],
) -> str:
    """
    Return a stable transaction type even when the LLM omits ``type``.

    A credit-card credit is treated as a payment because both a card top-up
    and a purchase refund reduce the outstanding balance. This rule is applied
    before category fallbacks so an incoming internal transfer to the credit
    card is not mistaken for an outgoing transfer.
    """
    normalized_explicit = (explicit_type or "").strip().lower()
    if normalized_explicit in SUPPORTED_TRANSACTION_TYPES - {"unknown"}:
        return normalized_explicit

    normalized_account = (account_type or "").strip().lower()
    normalized_category = (category or "").strip().lower()

    if normalized_account == "credit" and is_expense is False:
        return "payment"

    if "комисси" in normalized_category:
        return "fee"
    if "снятие" in normalized_category or "наличн" in normalized_category:
        return "withdrawal"
    if "перевод" in normalized_category:
        return "transfer"
    if "зачислен" in normalized_category or "пополнен" in normalized_category:
        return "deposit"

    if is_expense is True:
        return "purchase"
    if is_expense is False:
        return "deposit"
    return "unknown"


def is_credit_repayment(
    *,
    account_type: Optional[str],
    transaction_type: Optional[str],
    is_expense: Optional[bool],
) -> bool:
    """Whether the operation should reduce credit-card debt."""
    return (account_type or "").lower() == "credit" and (
        (transaction_type or "").lower() == "payment" or is_expense is False
    )
