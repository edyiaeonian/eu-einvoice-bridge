"""Render numbers into XML text without ever rounding them.

Both serializers use these, so the one place that decides how a Decimal
becomes text is also the one place that could reintroduce rounding. It
happened once: f"{value:.2f}" rounds with the context default, half-even,
and quietly undid the half-up policy the model applies.
"""

from decimal import Decimal

from .model import money
from .model.numeric import CENTS


def amount(value: Decimal) -> str:
    """A monetary amount, exact to the cent, rendered as-is.

    Rounding is the model's job and every amount arriving here is already exact
    to the cent, so anything else is a bug upstream and is raised rather than
    papered over.
    """
    exact = money(value)
    if exact != value:
        raise ValueError(
            f"amount {value} has more than two decimals; the model should have "
            f"refused it before serialization"
        )
    return f"{exact:f}"


def price(value: Decimal) -> str:
    """A unit price at its full precision.

    Unit price has no decimal limit in EN16931, so 0.125 is a legitimate price
    and must survive as 0.125. Prices with fewer than two decimals are padded
    to two for readability; padding adds zeros and never rounds.
    """
    normalized = value.normalize()
    if normalized.as_tuple().exponent > -2:
        normalized = normalized.quantize(CENTS)
    return f"{normalized:f}"


def quantity(value: Decimal) -> str:
    """A quantity without trailing zeros; :f keeps it out of exponent form."""
    return f"{value.normalize():f}"
