from fastapi import APIRouter

from app.deps import SessionDep, SignedIn
from app.schemas.common import ErrorOut
from app.schemas.portfolio import TenantAccountOut
from app.services import portfolio
from app.services.errors import Forbidden

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get(
    "/{tenant_id}",
    response_model=TenantAccountOut,
    responses={code: {"model": ErrorOut} for code in (401, 403, 404)},
)
def get_tenant(tenant_id: str, who: SignedIn, session: SessionDep) -> TenantAccountOut:
    """A tenant with the unit they occupy: `getTenantById`. The tenant
    themselves, or an admin. (A tenant's own record is also in /auth/me.)"""
    if who.tenant_id != tenant_id and who.kind != "admin":
        raise Forbidden("You can only see your own account.")
    return portfolio.get_tenant(session, tenant_id)
