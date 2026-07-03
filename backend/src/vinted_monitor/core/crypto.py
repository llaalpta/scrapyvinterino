from base64 import urlsafe_b64encode
from hashlib import sha256

from cryptography.fernet import Fernet


def encrypt_text(value: str, secret_key: str) -> str:
    return _fernet(secret_key).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str, secret_key: str) -> str:
    return _fernet(secret_key).decrypt(value.encode("utf-8")).decode("utf-8")


def fingerprint_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:12]


def mask_text(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"


def _fernet(secret_key: str) -> Fernet:
    digest = sha256(secret_key.encode("utf-8")).digest()
    return Fernet(urlsafe_b64encode(digest))
