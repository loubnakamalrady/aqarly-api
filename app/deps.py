"""What routes depend on: the database session, and who is asking.

A signed-in app sends its session token as the `X-Session` header. (Not
`Authorization`, which a locked staging API uses for its shared password: the
two travel together.) Routes take one of the `…Principal` types below and get
a `Principal`, or the request is refused before the route runs:
  - 401 when nobody is signed in or the session has ended,
  - 403 when someone is, but not someone this route is for.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.db import get_session
from app.services import auth
from app.services.auth import Principal
from app.services.errors import Forbidden, Unauthenticated

SessionDep = Annotated[Session, Depends(get_session)]

session_header = APIKeyHeader(
    name="X-Session",
    auto_error=False,
    description="The token from POST /auth/verify. In Swagger: Authorize, then paste it.",
)


def session_token(token: Annotated[str | None, Security(session_header)]) -> str | None:
    return token


def signed_in(session: SessionDep, token: Annotated[str | None, Depends(session_token)]) -> Principal:
    who = auth.principal(session, token, datetime.now(UTC))
    if who is None:
        raise Unauthenticated("Sign in to continue.")
    return who


SignedIn = Annotated[Principal, Depends(signed_in)]


def _admin(who: SignedIn) -> Principal:
    if who.kind != "admin":
        raise Forbidden("This is for the admin portals.")
    return who


def _ops(who: Annotated[Principal, Depends(_admin)]) -> Principal:
    if who.app != "ops":
        raise Forbidden("This is for the ops portal.")
    return who


def _tenant(who: SignedIn) -> Principal:
    if who.kind == "registration":
        raise Forbidden("Your building hasn't confirmed you yet.")
    if who.kind != "tenant":
        raise Forbidden("This is for tenants.")
    return who


def _staff(who: SignedIn) -> Principal:
    if who.kind != "staff":
        raise Forbidden("This is for the field app.")
    return who


AdminPrincipal = Annotated[Principal, Depends(_admin)]
OpsPrincipal = Annotated[Principal, Depends(_ops)]
TenantPrincipal = Annotated[Principal, Depends(_tenant)]
StaffPrincipal = Annotated[Principal, Depends(_staff)]


def own_trade(who: Principal, asked: str | None) -> str:
    """The trade an admin's read covers: their portal's. Asking for the other
    trade is refused rather than quietly answered with this one."""
    if asked is not None and asked != who.trade:
        raise Forbidden(f"This portal manages {who.trade} only.")
    assert who.trade is not None
    return who.trade
