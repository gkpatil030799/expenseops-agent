from __future__ import annotations

import re

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings


class SecretConfigurationError(RuntimeError):
    pass


_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _keyring() -> tuple[str, dict[str, Fernet]]:
    settings = get_settings()
    key = settings.app_secret_key
    if not key or key == "paste-a-generated-fernet-key-here":
        raise SecretConfigurationError(
            "APP_SECRET_KEY is missing. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    version = settings.app_secret_key_version.strip()
    if not _VERSION_PATTERN.fullmatch(version):
        raise SecretConfigurationError("APP_SECRET_KEY_VERSION must be a short safe identifier")
    keys = {version: Fernet(key.encode("utf-8"))}
    for value in settings.app_secret_key_previous:
        previous_version, separator, previous_key = value.partition(":")
        if not separator or not _VERSION_PATTERN.fullmatch(previous_version) or not previous_key:
            raise SecretConfigurationError(
                "APP_SECRET_KEY_PREVIOUS entries must use version:fernet-key"
            )
        if previous_version in keys:
            raise SecretConfigurationError(f"Duplicate encryption key version: {previous_version}")
        keys[previous_version] = Fernet(previous_key.encode("utf-8"))
    return version, keys


def encrypt_secret(value: str) -> str:
    if not value:
        raise ValueError("Cannot encrypt an empty secret")
    version, keys = _keyring()
    token = keys[version].encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{version}:{token}"


def decrypt_secret(value: str) -> str:
    active_version, keys = _keyring()
    version, separator, token = value.partition(":")
    try:
        if separator and version in keys:
            return keys[version].decrypt(token.encode("utf-8")).decode("utf-8")
        # Legacy ciphertexts had no key identifier. Trying every configured key
        # keeps existing integrations available during a staged rotation.
        ordered = [
            keys[active_version],
            *(key for name, key in keys.items() if name != active_version),
        ]
        return MultiFernet(ordered).decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretConfigurationError(
            "Unable to decrypt a stored token with the configured encryption keyring."
        ) from exc


def rotate_secret(value: str) -> str:
    """Re-encrypt a stored value with the active version without exposing plaintext."""
    active_version, _keys = _keyring()
    if value.startswith(f"{active_version}:"):
        return value
    return encrypt_secret(decrypt_secret(value))
