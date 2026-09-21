from enum import StrEnum


class VatCategory(StrEnum):
    """UNTDID 5305 VAT category codes (BT-118 / BT-151).

    The official list is `AE L M E S Z G O K B`.

    L and M (Spain) and B (Italy) are omitted: they carry no rules relevant to a
    Poland-facing project. O is omitted for a structural reason -- BR-O-05
    requires the rate to be *absent* rather than zero, and BR-O-11 to BR-O-14
    forbid mixing O with any other category on one invoice. That is a different
    shape from every other category, and modelling it would cost more than a
    rarely used code is worth here.
    """

    STANDARD = "S"
    ZERO_RATED = "Z"
    EXEMPT = "E"
    REVERSE_CHARGE = "AE"
    INTRA_COMMUNITY = "K"
    EXPORT = "G"

    @property
    def requires_exemption_reason(self) -> bool:
        """Whether BT-120/BT-121 must be present — and, inverted, must not be.

        Mirrors BR-E-10, BR-AE-10, BR-G-10 and BR-IC-10 (whose prefix is IC
        although the code is K), against BR-S-10 and BR-Z-10 which forbid a
        reason outright.
        """
        return self is not VatCategory.STANDARD and self is not VatCategory.ZERO_RATED

    @property
    def requires_positive_rate(self) -> bool:
        """BR-S-05 requires a rate above zero; BR-Z/E/AE/G/IC-05 require zero."""
        return self is VatCategory.STANDARD


class InvoiceTypeCode(StrEnum):
    """UNTDID 1001 document type codes (BT-3).

    Only the commercial invoice. A credit note (381) is a different UBL
    document, CreditNote rather than Invoice, and an Invoice carrying 381 fails
    BR-CL-01 fatally. Credit notes are out of scope, so the model refuses the
    code rather than accept something the serializer cannot emit validly.
    """

    COMMERCIAL_INVOICE = "380"
