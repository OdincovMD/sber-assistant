"""Optional API-key authentication for financial endpoints."""

import os
import secrets
from typing import Annotated, Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader


API_KEY_ENV = "API_KEY"
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def configured_api_key() -> Optional[str]:
    """Return the configured key, or None for compatibility mode."""
    value = os.getenv(API_KEY_ENV, "").strip()
    return value or None


def api_key_matches(provided_key: Optional[str]) -> bool:
    """Validate a supplied key without timing-sensitive string comparison."""
    expected_key = configured_api_key()
    if expected_key is None:
        return True
    if provided_key is None:
        return False
    return secrets.compare_digest(provided_key, expected_key)


async def require_api_key(
    provided_key: Annotated[Optional[str], Security(api_key_header)],
) -> None:
    """Protect an API route when API_KEY is configured."""
    if not api_key_matches(provided_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
