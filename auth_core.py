"""Shared staff/admin PIN utilities for the local Streamlit deployment.

These checks are an app-level guard, not an Internet-scale identity provider.
For public deployment, put the entire app behind an authenticated gateway and
use individual accounts, audit logs, and server-side rate limiting.
"""
from __future__ import annotations

import hashlib
import hmac


def strong_secret(value: str | None) -> bool:
    if not isinstance(value, str) or len(value) < 12 or value != value.strip():
        return False
    if len(set(value)) < 5:
        return False
    lowered = value.lower()
    return not any(bad in lowered for bad in ("123456", "password", "qwerty", "admin123", "000000"))


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_pin(entered: str, configured: str | None) -> bool:
    if not strong_secret(configured) or not isinstance(entered, str):
        return False
    return hmac.compare_digest(fingerprint(entered), fingerprint(configured))


def session_authorized(saved_fingerprint: str | None, current_secret: str | None) -> bool:
    return bool(strong_secret(current_secret)) and isinstance(saved_fingerprint, str) and hmac.compare_digest(
        saved_fingerprint, fingerprint(current_secret)
    )
