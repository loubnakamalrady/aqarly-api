from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import get_settings

# One engine per process: it owns the pool of open connections to Postgres.
# pool_pre_ping checks a pooled connection is still alive before handing it
# out, so a database restart doesn't surface as an error on the next request.
engine = create_engine(get_settings().database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed when it ends.

    Routes take it as `session: Session = Depends(get_session)`. Nothing is
    committed unless the route commits.
    """
    with SessionLocal() as session:
        yield session
