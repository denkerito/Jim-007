"""Password hashing and opaque-token helpers."""

import hashlib
import hmac
import secrets

from pwdlib import PasswordHash


_password_hash = PasswordHash.recommended()
_dummy_hash = _password_hash.hash("not-a-real-user-password")


class PasswordService:
    def hash(self, password: str) -> str:
        return _password_hash.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        return _password_hash.verify(password, password_hash)

    def verify_and_rehash(self, password: str, password_hash: str) -> tuple[bool, str | None]:
        return _password_hash.verify_and_update(password, password_hash)

    def verify_dummy(self, password: str) -> None:
        _password_hash.verify(password, _dummy_hash)


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(session_token: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), session_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()
