from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..auth import create_token, verify_navidrome

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    ok = await verify_navidrome(body.username, body.password)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return LoginResponse(token=create_token(body.username), username=body.username)
