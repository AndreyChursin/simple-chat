"""Argon2id password hashing. Hashing is CPU-bound (~tens of ms), so it runs in a worker thread
to keep the event loop (and WebSocket delivery) responsive."""

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with library-recommended parameters

# Used to spend the same time on unknown e-mails as on wrong passwords (no user enumeration).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def _verify_sync(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    return await anyio.to_thread.run_sync(_hasher.hash, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    if password_hash is None:
        await anyio.to_thread.run_sync(_verify_sync, _DUMMY_HASH, password)
        return False
    return await anyio.to_thread.run_sync(_verify_sync, password_hash, password)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
