"""Signing in with a phone number and a one-time code, and knowing who's asking.

The flow, the same in every app:
  1. `request_code`: a 6-digit code for this phone and app. While codes are
     shown on screen (settings.login_code_delivery == "screen") the caller
     gets it back to display; a texting provider replaces that later.
  2. `verify_code`: the right code, in time, opens a session. The token is
     returned once; only its hash is stored.
  3. `principal`: every later request presents the token and learns who it
     is: a tenant, a tenant waiting for approval, a phone with no account yet
     (tenant portal only, so they can register), a member of staff, or an admin
     of one portal.

Tenants can register themselves; ops approves them, which is what makes them
the unit's tenant. Staff and admins can't register: they're added by an admin.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Admin, AuthSession, LoginCode, Staff, Tenant, TenantRegistration, Unit
from app.models.enums import App, RequestType
from app.services.errors import Conflict, Forbidden, Invalid, NotFound, TooMany

CODE_LIFETIME = timedelta(minutes=10)
# Wrong guesses allowed per code: six digits can't be guessed in five tries.
MAX_ATTEMPTS = 5
# Codes one phone can ask for, per app, in a window.
MAX_CODES_PER_WINDOW = 5
CODE_WINDOW = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(days=30)

# The trade each admin portal manages.
ADMIN_TRADE: dict[App, RequestType] = {"ops": "maintenance", "housekeeping": "housekeeping"}

Kind = Literal["tenant", "registration", "new", "staff", "admin"]


@dataclass(frozen=True)
class Principal:
    """Who is asking. Exactly one of the ids is set, except for `new`: a
    tenant-portal phone that has been verified but has no account yet."""

    app: App
    phone: str
    tenant_id: str | None = None
    registration_id: str | None = None
    staff_id: str | None = None
    admin_id: str | None = None
    # An admin's portal's trade.
    trade: RequestType | None = None

    @property
    def kind(self) -> Kind:
        if self.tenant_id:
            return "tenant"
        if self.registration_id:
            return "registration"
        if self.staff_id:
            return "staff"
        if self.admin_id:
            return "admin"
        return "new"


# --- Phones --------------------------------------------------------------------


def normalize_phone(raw: str | None) -> str:
    """'+' and the digits: "+971 50 123 4567" and "+971501234567" are the same
    phone. Refuses what can't be a phone number at all."""
    digits = re.sub(r"[^0-9]", "", raw or "")
    if not 7 <= len(digits) <= 15:
        raise Invalid("Enter a valid mobile number")
    return "+" + digits


def _same_phone(column, phone: str):
    """Compares a stored, formatted phone (`+000 000 0001`) to a normalized one."""
    return func.regexp_replace(column, "[^0-9]", "", "g") == phone.removeprefix("+")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- Codes ---------------------------------------------------------------------


def request_code(session: Session, raw_phone: str | None, app: App, now: datetime) -> str:
    """A new code for this phone in this app. Any earlier one stops working.
    Always issued, even for a number with no account: whether a number has an
    account isn't said until the code is proved."""
    phone = normalize_phone(raw_phone)
    recent = session.scalar(
        select(func.count())
        .select_from(LoginCode)
        .where(LoginCode.phone == phone, LoginCode.app == app, LoginCode.created_at >= now - CODE_WINDOW)
    )
    if recent and recent >= MAX_CODES_PER_WINDOW:
        raise TooMany("Too many codes asked for. Wait a few minutes and try again.")

    session.execute(
        update(LoginCode)
        .where(LoginCode.phone == phone, LoginCode.app == app, LoginCode.used_at.is_(None))
        .values(expires_at=now)
    )
    code = f"{secrets.randbelow(10**6):06d}"
    session.add(
        LoginCode(
            phone=phone,
            app=app,
            code_hash=_hash(f"{app}:{phone}:{code}"),
            created_at=now,
            expires_at=now + CODE_LIFETIME,
            attempts=0,
        )
    )
    return code


def verify_code(session: Session, raw_phone: str | None, app: App, code: str | None, now: datetime) -> str:
    """Proves the code and opens a session; returns its token. A wrong guess
    is counted even though the request fails, so it commits that itself."""
    phone = normalize_phone(raw_phone)
    login = session.scalars(
        select(LoginCode)
        .where(
            LoginCode.phone == phone,
            LoginCode.app == app,
            LoginCode.used_at.is_(None),
            LoginCode.expires_at > now,
        )
        .order_by(LoginCode.created_at.desc())
        .limit(1)
        .with_for_update()
    ).first()
    if login is None:
        raise Invalid("That code has expired. Ask for a new one.")
    if login.attempts >= MAX_ATTEMPTS:
        raise Invalid("Too many wrong tries. Ask for a new code.")

    login.attempts += 1
    if not secrets.compare_digest(login.code_hash, _hash(f"{app}:{phone}:{(code or '').strip()}")):
        session.commit()  # the wrong guess counts, whatever happens next
        raise Invalid("That code isn't right. Check it and try again.")
    login.used_at = now

    account = _account(session, phone, app)
    if app == "field" and "staff_id" not in account:
        raise Forbidden("There's no staff account for this number. Ask your manager to add you.")
    if app in ADMIN_TRADE and "admin_id" not in account:
        raise Forbidden("This number isn't an admin of this portal.")

    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=_hash(token),
            app=app,
            phone=phone,
            created_at=now,
            expires_at=now + SESSION_LIFETIME,
            **account,
        )
    )
    return token


def _account(session: Session, phone: str, app: App) -> dict[str, str]:
    """Which account this phone opens in this app, as the session's foreign key."""
    if app == "tenant":
        tenant = session.scalars(select(Tenant).where(_same_phone(Tenant.phone, phone)).order_by(Tenant.id)).first()
        if tenant:
            return {"tenant_id": tenant.id}
        registration = session.scalars(
            select(TenantRegistration)
            .where(_same_phone(TenantRegistration.phone, phone))
            .order_by(TenantRegistration.created_at.desc())
        ).first()
        return {"registration_id": registration.id} if registration else {}
    if app == "field":
        member = session.scalars(
            select(Staff).where(_same_phone(Staff.phone, phone), Staff.retired_at.is_(None)).order_by(Staff.id)
        ).first()
        return {"staff_id": member.id} if member else {}
    admin = session.scalars(
        select(Admin)
        .where(_same_phone(Admin.phone, phone), Admin.trade == ADMIN_TRADE[app], Admin.retired_at.is_(None))
        .order_by(Admin.id)
    ).first()
    return {"admin_id": admin.id} if admin else {}


# --- Sessions ------------------------------------------------------------------


def principal(session: Session, token: str | None, now: datetime) -> Principal | None:
    """Who a session token belongs to, or None if it's unknown, ended or
    expired. Tenant-portal sessions are worked out from the phone each time,
    so approval (or ops adding the tenant) takes effect without signing in
    again."""
    if not token:
        return None
    found = session.get(AuthSession, _hash(token))
    if found is None or found.revoked_at is not None or found.expires_at <= now:
        return None

    if found.app == "tenant":
        return Principal(app="tenant", phone=found.phone, **_account(session, found.phone, "tenant"))
    if found.app == "field":
        member = session.get(Staff, found.staff_id) if found.staff_id else None
        if member is None or member.retired_at is not None:
            return None
        return Principal(app="field", phone=found.phone, staff_id=member.id)
    admin = session.get(Admin, found.admin_id) if found.admin_id else None
    if admin is None or admin.retired_at is not None:
        return None
    return Principal(app=found.app, phone=found.phone, admin_id=admin.id, trade=admin.trade)


def sign_out(session: Session, token: str | None, now: datetime) -> None:
    if token and (found := session.get(AuthSession, _hash(token))) and found.revoked_at is None:
        found.revoked_at = now


# --- Tenant registration ---------------------------------------------------------


def register(session: Session, who: Principal, name: str | None, unit_id: str | None, now: datetime) -> TenantRegistration:
    """A tenant-portal phone with no account says who they are and where they
    live. It waits for ops; until then they can sign in but see nothing of the
    unit's. After a decline they can register again."""
    if who.app != "tenant":
        raise Forbidden("Registration is for the tenant portal.")
    if who.kind == "tenant":
        raise Conflict("You're already registered.")
    if who.kind == "registration":
        current = session.get_one(TenantRegistration, who.registration_id)
        if current.decision is None:
            raise Conflict("Your registration is waiting for your building to confirm it.")

    name = (name or "").strip()
    if not name:
        raise Invalid("Enter your full name")
    if not unit_id or session.get(Unit, unit_id) is None:
        raise Invalid("Choose your building and unit")

    registration = TenantRegistration(
        id=f"reg-{secrets.token_hex(6)}", name=name, phone=who.phone, unit_id=unit_id, created_at=now
    )
    session.add(registration)
    return registration


def decide(session: Session, registration_id: str, admin_id: str, approve: bool, now: datetime) -> TenantRegistration:
    """Ops confirms (or declines) a registration. Approving makes a tenant and
    makes them the unit's tenant, replacing whoever was there: ops sees that
    before deciding."""
    registration = session.get(TenantRegistration, registration_id, with_for_update=True)
    if registration is None:
        raise NotFound(f"No registration {registration_id}")
    if registration.decision is not None:
        raise Conflict(f"That registration was already {registration.decision}.")

    if not approve:
        registration.decision, registration.decided_at, registration.decided_by = "declined", now, admin_id
        return registration

    tenant = Tenant(id=_tenant_id(session, registration.name), name=registration.name, phone=registration.phone, email=None)
    session.add(tenant)
    session.flush()
    unit = session.get_one(Unit, registration.unit_id)
    unit.tenant_id, unit.status = tenant.id, "occupied"
    # The decision and when it was made go in together: the table requires it.
    registration.decision, registration.decided_at, registration.decided_by = "approved", now, admin_id
    registration.tenant_id = tenant.id
    return registration


def _tenant_id(session: Session, name: str) -> str:
    """'ten-' and their last name, numbered if taken, like the seed's."""
    base = next((p for p in reversed(re.sub(r"[^a-z0-9]+", "-", name.lower()).split("-")) if p), "tenant")
    taken = set(session.scalars(select(Tenant.id).where(Tenant.id.like(f"ten-{base}%"))))
    candidate, suffix = f"ten-{base}", 2
    while candidate in taken:
        candidate, suffix = f"ten-{base}-{suffix}", suffix + 1
    return candidate
