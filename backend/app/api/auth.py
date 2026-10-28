"""Login, JWTs, and the per-request tenant-scoped database connection.

The tenant a request may see comes ONLY from the signed JWT, never from a
URL, query parameter or header the client controls.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, AsyncIterator, Literal

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import tenant_transaction
from app.models import Tenant, User

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
# Short-ish lifetime: a stateless JWT can't be revoked, so a deleted user or
# changed role only takes effect when the token expires.
TOKEN_TTL = timedelta(hours=8)
# Checked against when the email is unknown, so "no such user" takes as long
# as "wrong password" and response times don't reveal which emails exist.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
router = APIRouter(prefix="/auth", tags=["auth"])


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class CurrentUser(BaseModel):
    id: uuid.UUID
    tenant_id: str
    role: Literal["operator", "viewer"]


class Me(CurrentUser):
    email: str
    tenant_name: str


def current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> CurrentUser:
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.InvalidTokenError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"}
        )
    return CurrentUser(id=claims["sub"], tenant_id=claims["tenant_id"], role=claims["role"])


async def tenant_conn(
    request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> AsyncIterator[AsyncConnection]:
    """One transaction per request with SET LOCAL app.tenant_id (see
    app.db.set_tenant). Route handlers that take this connection can only
    ever see the caller's tenant, even if a query forgets a WHERE clause."""
    async with tenant_transaction(request.app.state.engine, user.tenant_id) as conn:
        yield conn


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
TenantConn = Annotated[AsyncConnection, Depends(tenant_conn)]


@router.post("/login")
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], request: Request) -> Token:
    """OAuth2 password form (field `username` = email), so the Swagger UI
    "Authorize" button works out of the box."""
    async with request.app.state.engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT * FROM auth_user_by_email(:email)"), {"email": form.username})
        ).one_or_none()

    password_hash = row.password_hash.encode() if row else _DUMMY_HASH
    # bcrypt is deliberately slow (~0.2 s); run it off the event loop so one
    # login doesn't stall every other request.
    password_ok = await asyncio.to_thread(bcrypt.checkpw, form.password.encode(), password_hash)
    if row is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")

    now = datetime.now(timezone.utc)
    claims = {"sub": str(row.id), "tenant_id": row.tenant_id, "role": row.role, "iat": now, "exp": now + TOKEN_TTL}
    return Token(access_token=jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM))


@router.get("/me")
async def me(user: CurrentUserDep, conn: TenantConn) -> Me:
    row = (
        await conn.execute(
            select(User.email, Tenant.name.label("tenant_name"))
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.id == user.id)
        )
    ).one()
    return Me(**user.model_dump(), email=row.email, tenant_name=row.tenant_name)
