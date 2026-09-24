"""Signing in and registering: the same phone-and-code flow in every app.

  POST /auth/code      → a code for this phone and app (shown on screen for now)
  POST /auth/verify    → the right code opens a session: { token, me }
  GET  /auth/me        → who the session is
  POST /auth/register  → a tenant-portal phone with no account says who they are
  POST /auth/logout    → ends the session
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.deps import SessionDep, SignedIn, session_token
from app.models import Admin, Staff, TenantRegistration
from app.schemas.auth import AdminOut, CodeIn, CodeOut, MeOut, RegisterIn, RegistrationOut, SessionOut, VerifyIn
from app.schemas.common import ErrorOut
from app.schemas.operations import PropertyOut, StaffOut, TenantOut, UnitOut
from app.services import auth, portfolio
from app.services.auth import Principal
from app.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

_REFUSALS = {code: {"model": ErrorOut} for code in (400, 401, 403, 409, 429)}


@router.post("/code", response_model=CodeOut, responses=_REFUSALS)
def request_code(body: CodeIn, session: SessionDep) -> CodeOut:
    """Sends a sign-in code for this phone and app. It's issued even for a
    number with no account, so this can't be used to find out who has one."""
    code = auth.request_code(session, body.phone, body.app, datetime.now(UTC))
    session.commit()
    shown = code if get_settings().login_code_delivery == "screen" else None
    return CodeOut(sent=True, shown_code=shown)


@router.post("/verify", response_model=SessionOut, responses=_REFUSALS)
def verify(body: VerifyIn, session: SessionDep) -> SessionOut:
    """Proves the code and opens a session. In the field app and the admin
    portals the number must already have an account; in the tenant portal a
    new number gets a session it can register with."""
    now = datetime.now(UTC)
    token = auth.verify_code(session, body.phone, body.app, body.code, now)
    session.commit()
    who = auth.principal(session, token, now)
    assert who is not None
    return SessionOut(token=token, me=me_out(session, who))


@router.get("/me", response_model=MeOut, responses={401: {"model": ErrorOut}})
def me(who: SignedIn, session: SessionDep) -> MeOut:
    return me_out(session, who)


@router.post("/register", response_model=MeOut, responses=_REFUSALS)
def register(body: RegisterIn, who: SignedIn, session: SessionDep) -> MeOut:
    """A tenant says who they are and which unit they live in. They wait for
    their building (ops) to confirm it before they see anything of the unit's."""
    now = datetime.now(UTC)
    registration = auth.register(session, who, body.name, body.unit_id, now)
    session.commit()
    return me_out(session, Principal(app="tenant", phone=who.phone, registration_id=registration.id))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(session: SessionDep, token: Annotated[str | None, Depends(session_token)]) -> Response:
    auth.sign_out(session, token, datetime.now(UTC))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def me_out(session: Session, who: Principal) -> MeOut:
    return MeOut(
        app=who.app,
        kind=who.kind,
        phone=who.phone,
        tenant=portfolio.get_tenant(session, who.tenant_id) if who.tenant_id else None,
        registration=registration_out(session.get_one(TenantRegistration, who.registration_id))
        if who.registration_id
        else None,
        staff=StaffOut.model_validate(session.get_one(Staff, who.staff_id)) if who.staff_id else None,
        admin=AdminOut.model_validate(session.get_one(Admin, who.admin_id)) if who.admin_id else None,
    )


def registration_out(registration: TenantRegistration) -> RegistrationOut:
    unit = registration.unit
    return RegistrationOut(
        id=registration.id,
        name=registration.name,
        phone=registration.phone,
        unit=UnitOut.model_validate(unit),
        property=PropertyOut.model_validate(unit.property),
        current_tenant=TenantOut.model_validate(unit.tenant) if unit.tenant else None,
        created_at=registration.created_at,
        decision=registration.decision,
        decided_at=registration.decided_at,
    )
