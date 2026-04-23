from __future__ import annotations

import hmac
from functools import lru_cache

from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.config import get_settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY is required")
    return Fernet(settings.encryption_key.encode("utf-8"))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


def validate_admin_token(provided_token: str | None) -> None:
    settings = get_settings()
    if not provided_token or not settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    if not hmac.compare_digest(provided_token, settings.admin_token):
        raise HTTPException(status_code=403, detail="Invalid admin token")

