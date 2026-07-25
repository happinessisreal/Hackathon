"""Password hashing and auth/RBAC dependencies.

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib `hashlib`, no extra
compiled dependency like bcrypt) as "<salt_hex>$<hash_hex>". Session auth is
a random opaque token stored in `users.token` (schema-locked column), sent as
`Authorization: Bearer <token>`. This is a deliberate simplification for a
single-session-per-user hackathon build, not a general-purpose auth system.
"""

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import User, Zone

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, digest_hex = password_hash.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return secrets.compare_digest(candidate.hex(), digest_hex)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def generate_api_key() -> str:
    return "zk_" + secrets.token_urlsafe(24)


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or malformed bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    result = await db.execute(select(User).where(User.token == token))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return user


async def get_current_user_ws(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    result = await db.execute(select(User).where(User.token == token))
    return result.scalar_one_or_none()


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def require_staff_or_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("staff", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff or admin role required")
    return user


async def get_current_zone(
    x_zone_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Zone:
    if not x_zone_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-Zone-Key header")
    result = await db.execute(select(Zone).where(Zone.api_key == x_zone_key))
    zone = result.scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unregistered zone key")
    return zone
