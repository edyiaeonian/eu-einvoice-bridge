"""What FA(3) cannot carry, found before anything is serialized.

One question decides every mismatch: would losing it change the amounts?

- No: the field is dropped and a warning names it (category 3).
- Yes, or FA(3) demands something the invoice lacks: an error, and no output
  (category 4, plus the category 3 fields that affect the tax base).

Format limits the schema itself enforces, such as decimal places, are left to
validating the output against the XSD rather than restated here.
"""

from ..model import Invoice, VatCategory
from ..validate.issues import Severity, ValidationIssue
from .mapping import rate_slot

# FA(3) carries an exemption reason only for exempt supplies, as P_19A/B/C text.
# The other categories that need one in EN16931 travel as a rate code alone.
_REASON_CARRIED = frozenset({VatCategory.EXEMPT})


def _issue(severity, rule_id, location, message, *terms) -> ValidationIssue:
    return ValidationIssue(
        source="mapping",
        severity=severity,
        rule_id=rule_id,
        location=location,
        message=message,
        business_terms=terms,
    )


def _errors(invoice: Invoice) -> list[ValidationIssue]:
    found = []
    extras = invoice.extras

    if extras is None:
        found.append(_issue(
            Severity.ERROR, "FA3-NO-DECLARATIONS", "extras",
            "FA(3) requires the Polish statutory declarations -- JST, GV and the "
            "Adnotacje annotations; give them in extras",
        ))

    vat_id = invoice.seller.vat_id
    if vat_id is None or not vat_id.startswith("PL"):
        found.append(_issue(
            Severity.ERROR, "FA3-SELLER-NIP", "seller.vat_id",
            "FA(3) identifies the seller by Polish NIP, so the seller needs a VAT "
            "identifier beginning with PL",
            "BT-31",
        ))

    if invoice.allowance_charges:
        found.append(_issue(
            Severity.ERROR, "FA3-DOC-ALLOWANCE", "allowance_charges",
            "document-level allowances and charges change the tax base (BR-S-08) "
            "and FA(3) has no document-level element that does; Rozliczenie "
            "adjusts only the payable amount, so carrying them would misstate the tax",
            "BG-20", "BG-21",
        ))

    for index, line in enumerate(invoice.lines):
        if rate_slot(line.vat_category, line.vat_rate) is None:
            found.append(_issue(
                Severity.ERROR, "FA3-RATE", f"lines.{index}.vat_rate",
                f"FA(3) has no code for VAT category {line.vat_category.value} at "
                f"rate {line.vat_rate}; its standard rates are 23/22, 8/7 and 5",
                "BT-151", "BT-152",
            ))

    exempt = [
        (index, line)
        for index, line in enumerate(invoice.lines)
        if line.vat_category is VatCategory.EXEMPT
    ]
    if exempt and extras is not None and extras.exemption_basis is None:
        found.append(_issue(
            Severity.ERROR, "FA3-EXEMPTION-BASIS", "extras.exemption_basis",
            "an exempt invoice must say whether the exemption rests on Polish law, "
            "the VAT Directive or another basis (P_19A, P_19B or P_19C)",
        ))
    for index, line in exempt:
        if line.exemption_reason is None:
            found.append(_issue(
                Severity.ERROR, "FA3-EXEMPTION-TEXT", f"lines.{index}.exemption_reason",
                "FA(3) needs the exemption's legal basis as text; a reason code "
                "alone cannot fill P_19A/B/C",
                "BT-120",
            ))

    if extras is not None and extras.margin_scheme:
        found.append(_issue(
            Severity.ERROR, "FA3-MARGIN-SCHEME", "extras.margin_scheme",
            "margin schemes change how the tax is computed and are not supported",
        ))
    if extras is not None and extras.intra_eu_new_means_of_transport:
        found.append(_issue(
            Severity.ERROR, "FA3-NEW-TRANSPORT", "extras.intra_eu_new_means_of_transport",
            "an intra-EU supply of new means of transport needs vehicle details "
            "that FA(3) requires and this model does not carry",
        ))

    return found


def _warnings(invoice: Invoice) -> list[ValidationIssue]:
    found = []

    for role in ("seller", "buyer"):
        if getattr(invoice, role).legal_registration_id is not None:
            found.append(_issue(
                Severity.WARNING, "FA3-DROP-BT30", f"{role}.legal_registration_id",
                "FA(3) records only Polish KRS or REGON numbers, and nothing says "
                "which register this identifier belongs to, so it is left out",
                "BT-30",
            ))

    for index, line in enumerate(invoice.lines):
        position = index + 1
        if line.line_id != str(position):
            found.append(_issue(
                Severity.WARNING, "FA3-DROP-LINE-ID", f"lines.{index}.line_id",
                f"FA(3) numbers lines with positive integers, so this is line "
                f"{position} and its identifier {line.line_id!r} is not carried",
                "BT-126",
            ))
        if line.exemption_reason_code is not None:
            found.append(_issue(
                Severity.WARNING, "FA3-DROP-BT121", f"lines.{index}.exemption_reason_code",
                "FA(3) has no field for an exemption reason code; only reason text "
                "can be carried",
                "BT-121",
            ))
        if line.exemption_reason is not None and line.vat_category not in _REASON_CARRIED:
            found.append(_issue(
                Severity.WARNING, "FA3-DROP-REASON", f"lines.{index}.exemption_reason",
                f"FA(3) signals category {line.vat_category.value} by its rate code "
                f"alone and has no field for the reason text",
                "BT-120",
            ))

    return found


def fa3_issues(invoice: Invoice) -> list[ValidationIssue]:
    """Everything standing between this invoice and a faithful FA(3), at once.

    Errors come first, since they decide whether there is any output at all.
    """
    return _errors(invoice) + _warnings(invoice)
