from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator

CENTS = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Round a monetary amount to two decimals, half away from zero.

    The mode is stated explicitly because Python's default is ROUND_HALF_EVEN
    (banker's rounding), which disagrees with tax convention at exactly .005 and
    would do so silently: 0.005 becomes 0.00 under the default and 0.01 here.
    XPath's round(), which the official Schematron uses, rounds half upwards, so
    this matches what the rules check against.
    """
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _reject_float(value: Any) -> Any:
    """Refuse float at the boundary.

    Pydantic would coerce 0.1 into Decimal("0.1") and look correct, which is the
    problem: by the time a float arrives any arithmetic behind it has already
    lost precision (0.1 + 0.2 == 0.30000000000000004), and coercion would make
    that loss permanent and silent. A cent of drift is enough for a tax
    authority to reject the invoice.
    """
    if isinstance(value, float):
        raise ValueError(
            "float is not accepted for exact amounts; pass Decimal or str "
            "(a float has already lost precision before reaching this point)"
        )
    return value


ExactDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]
