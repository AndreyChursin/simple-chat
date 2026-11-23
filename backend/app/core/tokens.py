import hashlib
import secrets


def new_token() -> str:
    """Opaque random token for cookies and e-mail links (256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """Only the SHA-256 of a token is stored; a DB leak does not reveal usable tokens."""
    return hashlib.sha256(token.encode("utf-8")).digest()
