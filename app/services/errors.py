"""Refusals a service can raise. `app/main.py` turns each into an HTTP status
with `{"detail": message}`, so services never deal in HTTP themselves and the
message reaches the frontend as written. Say what's wrong in words a person
can act on."""


class ServiceError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(ServiceError):
    """The thing asked for doesn't exist. → 404"""


class Forbidden(ServiceError):
    """It exists, but not for whoever asked. → 403"""


class Conflict(ServiceError):
    """The thing's current state doesn't allow this (a closed job, a job not
    started yet). → 409"""


class Invalid(ServiceError):
    """What was sent can't be accepted as it is (too few photos, no reason).
    → 400. FastAPI's own 422 still covers a body of the wrong shape."""
