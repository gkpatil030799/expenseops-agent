from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class SecretConfigurationError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().app_secret_key
    if not key or key == "paste-a-generated-fernet-key-here":
        raise SecretConfigurationError(
            "APP_SECRET_KEY is missing. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode("utf-8"))


def encrypt_secret(value: str) -> str:
    if not value:
        raise ValueError("Cannot encrypt an empty secret")
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretConfigurationError(
            "Unable to decrypt a stored token. Confirm APP_SECRET_KEY matches the key used "
            "when the token was saved."
        ) from exc
