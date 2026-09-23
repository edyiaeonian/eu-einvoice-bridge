from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator

CENTS = Decimal("0.01")

# Upper bound on the digits of any numeric input. Decimal's default context has
# 28 digits of precision, and quantize() past that raises InvalidOperation -- an
# ArithmeticError Pydantic does not convert, so "1E+30" crashed the CLI instead
# of being reported. Eighteen digits leaves room for sums and products of
# bounded inputs while being far beyond any real invoice.
MAX_DIGITS = 18


def money(value: Decimal) -> Decimal:
    """Round a monetary amount to two decimals, half away from zero.

    The mode is stated explicitly because Python's default is ROUND_HALF_EVEN
    (banker's rounding), which disagrees with tax convention at exactly .005 and
    would do so silently: 0.005 becomes 0.00 under the default and 0.01 here.

    This is not quite XPath's round(), which the official Schematron uses: that
    rounds half towards positive infinity, so round(-2.5) is -2 where this gives
    -3. The two agree on every non-negative amount, and the rules that recompute
    a rounded amount (BR-CO-17, for one) take abs() before rounding. A negative
    amount does occur -- a VAT group whose document-level allowances exceed its
    lines -- and for it this follows tax convention, symmetric about zero.
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


def _at_most_two_decimals(value: Decimal) -> Decimal:
    """Refuse, rather than round, a monetary amount with sub-cent digits.

    EN16931 caps these at two decimals, fatally: BR-DEC-01 and -05 for
    allowances and charges, -16 for the paid amount, -23 for a line's net
    amount. Rounding here would hide the input error, and rounding later is
    worse -- that is how half-even formatting in the serializer came to
    disagree with the half-up breakdown.

    Trailing zeros are not extra precision: 10.500 is accepted and stored as
    10.50, because the value, not its spelling, is what has two decimals.
    """
    exact = value.quantize(CENTS, rounding=ROUND_HALF_UP)
    if exact != value:
        raise ValueError(
            f"at most two decimal places are allowed for a monetary amount, "
            f"got {value}"
        )
    return exact


# A monetary amount whose value has at most two decimal places.
Amount = Annotated[ExactDecimal, AfterValidator(_at_most_two_decimals)]
