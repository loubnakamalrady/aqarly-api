from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.common import ErrorOut
from app.schemas.portfolio import TenantAccountOut
from app.services import portfolio

router = APIRouter(prefix="/tenants", tags=["tenants"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/{tenant_id}", response_model=TenantAccountOut, responses={404: {"model": ErrorOut}})
def get_tenant(tenant_id: str, session: SessionDep) -> TenantAccountOut:
    """A tenant with the unit they occupy: `getTenantById`. The tenant
    portal's notifications and history are built from `/requests?tenantId=`."""
    return portfolio.get_tenant(session, tenant_id)
