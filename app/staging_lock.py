"""The staging lock: one shared username and password in front of the whole
API, because nothing signs anyone in yet (roadmap Phase 9). Off unless
STAGING_PASSWORD is set, so local development is unaffected.

A browser opening /docs gets its built-in login prompt; the frontends send the
same credentials with every server-side call (packages/core/src/api.ts). This
keeps strangers out of a deployed copy. It is not user accounts, and knowing
the password gives full access.
"""

import base64
import binascii
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.settings import get_settings

# Left open so the host can check the API is up without the password.
OPEN_PATHS = {"/health"}


async def staging_lock(request: Request, call_next: RequestResponseEndpoint) -> Response:
    settings = get_settings()
    if not settings.staging_password or request.url.path in OPEN_PATHS:
        return await call_next(request)
    if _allowed(request.headers.get("authorization"), settings.staging_user, settings.staging_password):
        return await call_next(request)
    return JSONResponse(
        status_code=401,
        content={"detail": "This is a private staging server. Sign in with the staging password."},
        # Makes a browser show its login prompt.
        headers={"WWW-Authenticate": 'Basic realm="Aqarly staging", charset="UTF-8"'},
    )


def _allowed(header: str | None, user: str, password: str) -> bool:
    """HTTP Basic auth: `Basic base64(user:password)`. Compared in constant
    time, so response timing doesn't leak how much of a guess was right."""
    scheme, _, encoded = (header or "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        given_user, _, given_password = base64.b64decode(encoded, validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return secrets.compare_digest(given_user.encode(), user.encode()) & secrets.compare_digest(
        given_password.encode(), password.encode()
    )
