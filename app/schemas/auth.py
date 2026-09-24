from datetime import datetime

from app.models.enums import App, RegistrationDecision, RequestType
from app.schemas.common import CamelModel
from app.schemas.operations import PropertyOut, StaffOut, TenantOut, UnitOut
from app.schemas.portfolio import TenantAccountOut
from app.services.auth import Kind


class CodeIn(CamelModel):
    phone: str
    app: App


class CodeOut(CamelModel):
    sent: bool
    # The code itself, only while codes are shown on screen rather than
    # texted (development and staging). Never set once texting is on.
    shown_code: str | None


class VerifyIn(CamelModel):
    phone: str
    app: App
    code: str


class RegisterIn(CamelModel):
    name: str | None = None
    unit_id: str | None = None


class AdminOut(CamelModel):
    id: str
    name: str
    phone: str
    trade: RequestType


class UnitChoiceOut(CamelModel):
    """A unit as registration offers it: enough to pick yours."""

    id: str
    label: str


class RegistrationOut(CamelModel):
    id: str
    name: str
    phone: str
    unit: UnitOut
    property: PropertyOut
    # Who the unit belongs to now: approving replaces them.
    current_tenant: TenantOut | None
    created_at: datetime
    decision: RegistrationDecision | None
    decided_at: datetime | None


class MeOut(CamelModel):
    """Who is signed in. `kind` says which of the records below is set:
    `tenant`; `registration` (waiting for ops, or declined); `new` (a proved
    phone with no account yet, which can register); `staff`; `admin`."""

    app: App
    kind: Kind
    phone: str
    tenant: TenantAccountOut | None = None
    registration: RegistrationOut | None = None
    staff: StaffOut | None = None
    admin: AdminOut | None = None


class SessionOut(CamelModel):
    """A new session. Keep `token` (in an HttpOnly cookie) and send it as the
    `X-Session` header on every call; it isn't shown again."""

    token: str
    me: MeOut
