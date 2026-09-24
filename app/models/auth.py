"""Accounts and signing in.

Everyone signs in with a phone number and a one-time code. Which account the
phone opens depends on the app: a tenant in the tenant portal, a member of
staff in the field app, an admin in the ops or housekeeping portal. Codes and
sessions are stored hashed: a leaked table gives nobody a way in.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import App, RegistrationDecision, RequestType
from app.models.property import Unit


class Admin(Base):
    """Someone who runs one of the admin portals: ops (maintenance) or
    housekeeping. Not staff: admins assign work, staff do it."""

    __tablename__ = "admins"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    # The portal they run, by the trade it manages.
    trade: Mapped[RequestType]
    retired_at: Mapped[datetime | None]


class TenantRegistration(Base):
    """A tenant who registered themselves, waiting for ops to confirm they
    live in the unit they picked. Approving creates the tenant and makes them
    the unit's tenant; until then they can sign in but see nothing of the
    unit's."""

    __tablename__ = "tenant_registrations"
    __table_args__ = (
        CheckConstraint("(decision IS NULL) = (decided_at IS NULL)", name="decision_and_time_together"),
        CheckConstraint("decision = 'approved' OR tenant_id IS NULL", name="only_approved_have_a_tenant"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    created_at: Mapped[datetime]
    decision: Mapped[RegistrationDecision | None]
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("admins.id"))
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"))

    unit: Mapped[Unit] = relationship()


class LoginCode(Base):
    """A one-time code sent to a phone for one app. Only its hash is kept.
    Used once, dead after `expires_at`, and dead after too many wrong
    guesses."""

    __tablename__ = "login_codes"
    __table_args__ = (Index("ix_login_codes_phone_app", "phone", "app"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Digits and a leading +, as `normalize_phone` makes them.
    phone: Mapped[str] = mapped_column(String(20))
    app: Mapped[App]
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    used_at: Mapped[datetime | None]


class AuthSession(Base):
    """A signed-in browser. The app holds the token in a cookie; only its hash
    is stored. It points at whichever account signed in, or at none yet when
    a tenant has proved their phone but not registered."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    app: Mapped[App]
    phone: Mapped[str] = mapped_column(String(20))
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    registration_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenant_registrations.id", ondelete="CASCADE")
    )
    staff_id: Mapped[str | None] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"))
    admin_id: Mapped[str | None] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
