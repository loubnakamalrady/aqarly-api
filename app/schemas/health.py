from typing import Literal

from pydantic import BaseModel


class Health(BaseModel):
    ok: bool
    database: Literal["connected", "unreachable"]
