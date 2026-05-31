"""JWT auth backed by Navidrome credential verification."""
import hashlib
import secrets
from datetime import datetime, timedelta

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import settings

bearer = HTTPBearer()


def _subsonic_token(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(6)
    token = hashlib.md5(f"{password}{salt}".encode()).hexdigest()
    return token, salt


async def verify_navidrome(username: str, password: str) -> bool:
    token, salt = _subsonic_token(password)
    params = {
        "u": username,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "kwhale",
        "f": "json",
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(
                f"{settings.navidrome_url}/rest/ping.view", params=params
            )
            data = r.json().get("subsonic-response", {})
            return data.get("status") == "ok"
        except Exception:
            return False


def create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.jwt_expire_hours)
    return jwt.encode(
        {"sub": username, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    return decode_token(creds.credentials)
