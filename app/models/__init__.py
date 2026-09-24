"""SQLAlchemy tables.

Every model module must be imported here: Alembic compares `Base.metadata`
against the database, and a model it never imported is a table it never sees.
"""

from app.models.auth import Admin, AuthSession, LoginCode, TenantRegistration
from app.models.base import Base
from app.models.housekeeping_rate import HousekeepingRate
from app.models.listing import Listing
from app.models.people import Staff, Tenant
from app.models.property import Property, Unit
from app.models.service_request import CompletionPhoto, Photo, ServiceRequest, StageEntry

__all__ = [
    "Admin",
    "AuthSession",
    "Base",
    "LoginCode",
    "TenantRegistration",
    "CompletionPhoto",
    "HousekeepingRate",
    "Listing",
    "Photo",
    "Property",
    "ServiceRequest",
    "StageEntry",
    "Staff",
    "Tenant",
    "Unit",
]
