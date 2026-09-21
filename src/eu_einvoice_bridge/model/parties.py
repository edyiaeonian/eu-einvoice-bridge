from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CountryCode = Annotated[
    str, StringConstraints(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
]


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    street: str = Field(min_length=1)
    city: str = Field(min_length=1)
    postal_code: str = Field(min_length=1)
    country: CountryCode


class Party(BaseModel):
    """A seller or buyer (BG-4 / BG-7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    # BT-30 and BT-31 identify a party in different registers and are not
    # interchangeable. A buyer may legitimately have neither.
    legal_registration_id: str | None = None
    vat_id: str | None = None
    vat_scheme: CountryCode
    address: Address
