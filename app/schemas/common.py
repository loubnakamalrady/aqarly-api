from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

# Money is `Decimal` in the database and a JSON number on the wire, because
# `types.ts` declares it `number`. (Pydantic would send a `Decimal` as a
# string.) Amounts are whole or 2-decimal currency, which a float holds exactly
# enough for display; nothing on the frontend does arithmetic it has to trust.
Money = float


class CamelModel(BaseModel):
    """Base for every response shape.

    Python fields are snake_case like the database (`unit_id`); the JSON, and
    the OpenAPI schema the frontend's TypeScript is generated from, use the
    camelCase names `types.ts` has (`unitId`). `from_attributes` lets a schema
    read straight off a SQLAlchemy object.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        from_attributes=True,
    )


class ErrorOut(BaseModel):
    """Every refusal: a message the frontend can show as it is."""

    detail: str
