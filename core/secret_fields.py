"""Central secret-field alias classification shared by authority and audit paths."""
from __future__ import annotations

import re

_BENIGN_FIELDS = frozenset({
    "token", "tokenizer", "tokencount", "maxtokens", "cookiedomain",
})
_SENSITIVE_EXACT = frozenset({
    "credentialref", "credential", "credentials", "authorization", "bearer",
    "cookie", "accesskey", "sessionid", "password", "passwd", "dbpasswd",
    "passphrase", "pwd", "secret", "servicesecret", "privatekey",
    "sessionkey", "signingkey", "encryptionkey", "secretkey",
    "awssecretaccesskey", "jwttoken", "authtoken", "githubtoken",
    "bearertoken", "apitoken", "sessiontoken", "idtoken", "accesstoken",
    "refreshtoken", "apikey", "apisecret", "clientsecret",
})
_TOKEN_SUFFIXES = (
    "apitoken", "authtoken", "jwttoken", "sessiontoken", "idtoken",
    "accesstoken", "refreshtoken", "bearertoken", "githubtoken",
)
_SECRET_SUFFIXES = ("apisecret", "clientsecret", "servicesecret", "secret")
_PASSWORD_SUFFIXES = ("password", "passwd", "passphrase", "pwd")
_KEY_SUFFIX_MARKERS = (
    "api", "access", "secret", "private", "signing", "encryption",
    "credential", "aws", "session",
)
_CREDENTIAL_SUFFIXES = ("credentialref", "credential", "credentials")


def normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def is_sensitive_field(value: str) -> bool:
    normalized = normalize_field_name(value)
    if normalized in _BENIGN_FIELDS:
        return False
    if normalized in _SENSITIVE_EXACT:
        return True
    if normalized.endswith(_TOKEN_SUFFIXES + _SECRET_SUFFIXES + _PASSWORD_SUFFIXES + _CREDENTIAL_SUFFIXES):
        return True
    return normalized.endswith("key") and any(
        marker in normalized[:-3] for marker in _KEY_SUFFIX_MARKERS
    )


__all__ = ["is_sensitive_field", "normalize_field_name"]
