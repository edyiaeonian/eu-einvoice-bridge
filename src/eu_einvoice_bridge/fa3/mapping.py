"""Where EN16931's concepts land in FA(3), and where they cannot.

The rate table is the project's premise in miniature: EN16931 describes tax as
a category plus any rate, FA(3) as one of a closed set of Polish codes with a
subtotal slot for each. A standard rate Poland does not levy has nowhere to go.
"""

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from lxml import etree

from ..model import VatCategory
from ..paths import FA3_XSD

FA3_NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"
_XS = "{http://www.w3.org/2001/XMLSchema}"


@dataclass(frozen=True, slots=True)
class RateSlot:
    code: str  # P_12 on the line
    bucket: str  # suffix of P_13_x (net) and P_14_x (tax)
    taxed: bool  # whether the bucket has a P_14_x tax slot at all


# P_13_1 is documented as "currently 23% or 22%", P_13_2 as "8% or 7%".
_STANDARD = {
    Decimal("0.23"): RateSlot("23", "1", True),
    Decimal("0.22"): RateSlot("22", "1", True),
    Decimal("0.08"): RateSlot("8", "2", True),
    Decimal("0.07"): RateSlot("7", "2", True),
    Decimal("0.05"): RateSlot("5", "3", True),
}

_UNTAXED = {
    VatCategory.ZERO_RATED: RateSlot("0 KR", "6_1", False),
    VatCategory.INTRA_COMMUNITY: RateSlot("0 WDT", "6_2", False),
    VatCategory.EXPORT: RateSlot("0 EX", "6_3", False),
    VatCategory.EXEMPT: RateSlot("zw", "7", False),
    VatCategory.REVERSE_CHARGE: RateSlot("oo", "10", False),
}

# The buckets this mapping can produce, in the schema's sequence order.
BUCKET_ORDER = ("1", "2", "3", "6_1", "6_2", "6_3", "7", "10")


def rate_slot(category: VatCategory, rate: Decimal) -> RateSlot | None:
    """The FA(3) code and subtotal slot for a category and rate, if any."""
    if category is VatCategory.STANDARD:
        return _STANDARD.get(rate)
    return _UNTAXED.get(category)


@lru_cache(maxsize=1)
def eu_vat_prefixes() -> frozenset[str]:
    """The EU VAT prefixes FA(3) accepts, read from the vendored schema.

    Read rather than copied so the two cannot drift. The list holds VAT
    prefixes, not ISO country codes: Greece is EL, and Northern Ireland XI.
    """
    root = etree.parse(str(FA3_XSD)).getroot()
    codes = root.find(f".//{_XS}simpleType[@name='TKodyKrajowUE']")
    return frozenset(e.get("value") for e in codes.iter(f"{_XS}enumeration"))
