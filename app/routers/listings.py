from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Listing
from app.schemas.common import ErrorOut
from app.schemas.listing import ListingOut
from app.services.errors import NotFound

router = APIRouter(prefix="/listings", tags=["listings"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[ListingOut])
def list_listings(
    session: SessionDep,
    purpose: str | None = None,
    listing_type: Annotated[str | None, Query(alias="type")] = None,
    featured: bool | None = None,
) -> list[ListingOut]:
    """The marketing site's listings: `getProperties()` in the frontend.
    Each filter is optional and they combine."""
    query = select(Listing).order_by(Listing.slug)
    if purpose is not None:
        query = query.where(Listing.purpose == purpose)
    if listing_type is not None:
        query = query.where(Listing.type == listing_type)
    if featured is not None:
        query = query.where(Listing.featured == featured)
    return [ListingOut.from_model(listing) for listing in session.scalars(query)]


@router.get("/{slug}", response_model=ListingOut, responses={404: {"model": ErrorOut}})
def get_listing(slug: str, session: SessionDep) -> ListingOut:
    """One listing: `getPropertyBySlug()` in the frontend."""
    listing = session.get(Listing, slug)
    if listing is None:
        raise NotFound(f"No listing {slug}")
    return ListingOut.from_model(listing)
