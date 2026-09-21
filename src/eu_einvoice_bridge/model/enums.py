from enum import StrEnum


class VatCategory(StrEnum):
    """UNTDID 5305 VAT category codes (BT-118 / BT-151).

    The official list is `AE L M E S Z G O K B`. L and M (Spain) and B (Italy)
    are omitted: they carry no rules relevant to a Poland-facing project and
    modelling them without those rules would be worse than leaving them out.
    """

    STANDARD = "S"
    ZERO_RATED = "Z"
    EXEMPT = "E"
    REVERSE_CHARGE = "AE"
    INTRA_COMMUNITY = "K"
    EXPORT = "G"
    OUT_OF_SCOPE = "O"

    @property
    def requires_exemption_reason(self) -> bool:
        """Whether BT-120/BT-121 must be present — and, inverted, must not be.

        Mirrors BR-E-10, BR-AE-10, BR-G-10, BR-O-10 and BR-IC-10 (whose prefix
        is IC although the code is K), against BR-S-10 and BR-Z-10 which forbid
        a reason outright.
        """
        return self is not VatCategory.STANDARD and self is not VatCategory.ZERO_RATED


class InvoiceTypeCode(StrEnum):
    """UNTDID 1001 document type codes (BT-3)."""

    COMMERCIAL_INVOICE = "380"
    CREDIT_NOTE = "381"
