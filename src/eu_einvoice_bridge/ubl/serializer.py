"""Serialize the neutral model to EN16931 in UBL 2.1 syntax.

UBL element order is fixed by its XSD, so the order things are appended here is
load-bearing and follows the official examples rather than intuition. The
clearest trap: inside cac:Item the optional Description precedes the mandatory
Name.
"""

from decimal import Decimal

from lxml import etree

from ..formatting import amount as _amount
from ..formatting import price as _price
from ..formatting import quantity as _quantity
from ..model import AllowanceCharge, Invoice, LineItem, Party, VatCategory

INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

NSMAP = {None: INVOICE_NS, "cac": CAC, "cbc": CBC}

# BT-24. Plain EN16931 rather than a CIUS: no Peppol BIS or national profile
# rules are implemented, so claiming one would invite validation this project
# does not satisfy.
CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017"

VAT_SCHEME_ID = "VAT"


def _el(parent, ns: str, name: str, text: str | None = None, **attrs):
    node = etree.SubElement(parent, f"{{{ns}}}{name}", **attrs)
    if text is not None:
        node.text = text
    return node


def _percent(rate: Decimal) -> str:
    """BT-119 is a percentage while the model holds a fraction.

    0.23 becomes "23". normalize() drops the trailing zeros that would otherwise
    make it "23.00", and :f keeps large or small values out of exponent form.
    """
    return f"{(rate * 100).normalize():f}"


def _tax_category(
    parent,
    element_name: str,
    category: VatCategory,
    rate: Decimal,
    *,
    exemption_reason: str | None = None,
    exemption_reason_code: str | None = None,
) -> None:
    """cac:TaxCategory — ID, Percent, exemption reason, then TaxScheme.

    The exemption reason is passed explicitly rather than read off whatever
    object arrives, because it belongs in only one of the three places a tax
    category appears: the VAT breakdown carries BT-120/BT-121, and UBL-CR-601 --
    a warning-level rule -- says a line's ClassifiedTaxCategory should not.
    Keeping to the warning keeps this serializer's output free of warnings too.
    """
    node = _el(parent, CAC, element_name)
    _el(node, CBC, "ID", category.value)
    _el(node, CBC, "Percent", _percent(rate))

    if exemption_reason_code is not None:
        _el(node, CBC, "TaxExemptionReasonCode", exemption_reason_code)
    if exemption_reason is not None:
        _el(node, CBC, "TaxExemptionReason", exemption_reason)

    scheme = _el(node, CAC, "TaxScheme")
    _el(scheme, CBC, "ID", VAT_SCHEME_ID)


def _party(parent, wrapper: str, party: Party) -> None:
    node = _el(parent, CAC, wrapper)
    body = _el(node, CAC, "Party")

    address = _el(body, CAC, "PostalAddress")
    _el(address, CBC, "StreetName", party.address.street)
    _el(address, CBC, "CityName", party.address.city)
    _el(address, CBC, "PostalZone", party.address.postal_code)
    country = _el(address, CAC, "Country")
    _el(country, CBC, "IdentificationCode", party.address.country)

    # BT-31 lives under PartyTaxScheme, BT-30 under PartyLegalEntity. They are
    # different registers and must not be collapsed into one element.
    if party.vat_id is not None:
        tax_scheme = _el(body, CAC, "PartyTaxScheme")
        _el(tax_scheme, CBC, "CompanyID", party.vat_id)
        scheme = _el(tax_scheme, CAC, "TaxScheme")
        _el(scheme, CBC, "ID", VAT_SCHEME_ID)

    legal = _el(body, CAC, "PartyLegalEntity")
    _el(legal, CBC, "RegistrationName", party.name)
    if party.legal_registration_id is not None:
        _el(legal, CBC, "CompanyID", party.legal_registration_id)


def _allowance_charge(parent, item: AllowanceCharge, currency: str) -> None:
    node = _el(parent, CAC, "AllowanceCharge")
    _el(node, CBC, "ChargeIndicator", "true" if item.is_charge else "false")
    if item.reason is not None:
        _el(node, CBC, "AllowanceChargeReason", item.reason)
    _el(node, CBC, "Amount", _amount(item.amount), currencyID=currency)
    _tax_category(node, "TaxCategory", item.vat_category, item.vat_rate)


def _tax_total(parent, invoice: Invoice) -> None:
    node = _el(parent, CAC, "TaxTotal")
    _el(
        node,
        CBC,
        "TaxAmount",
        _amount(invoice.totals.total_vat),
        currencyID=invoice.currency,
    )
    for entry in invoice.vat_breakdown:
        subtotal = _el(node, CAC, "TaxSubtotal")
        _el(
            subtotal,
            CBC,
            "TaxableAmount",
            _amount(entry.taxable_amount),
            currencyID=invoice.currency,
        )
        _el(
            subtotal,
            CBC,
            "TaxAmount",
            _amount(entry.tax_amount),
            currencyID=invoice.currency,
        )
        _tax_category(
            subtotal,
            "TaxCategory",
            entry.category,
            entry.rate,
            exemption_reason=entry.exemption_reason,
            exemption_reason_code=entry.exemption_reason_code,
        )


def _monetary_total(parent, invoice: Invoice) -> None:
    totals = invoice.totals
    currency = invoice.currency
    node = _el(parent, CAC, "LegalMonetaryTotal")

    def amount(name: str, value: Decimal) -> None:
        _el(node, CBC, name, _amount(value), currencyID=currency)

    # UBL's order here is not the order of the BT numbers.
    amount("LineExtensionAmount", totals.sum_line_net)  # BT-106
    amount("TaxExclusiveAmount", totals.total_without_vat)  # BT-109
    amount("TaxInclusiveAmount", totals.total_with_vat)  # BT-112
    if invoice.allowance_charges:
        amount("AllowanceTotalAmount", totals.allowances_total)  # BT-107
        amount("ChargeTotalAmount", totals.charges_total)  # BT-108
    if totals.prepaid_amount:
        amount("PrepaidAmount", totals.prepaid_amount)  # BT-113
    amount("PayableAmount", totals.amount_due)  # BT-115


def _invoice_line(parent, line: LineItem, currency: str) -> None:
    node = _el(parent, CAC, "InvoiceLine")
    _el(node, CBC, "ID", line.line_id)
    _el(
        node,
        CBC,
        "InvoicedQuantity",
        _quantity(line.quantity),
        unitCode=line.unit_code,
    )
    _el(
        node,
        CBC,
        "LineExtensionAmount",
        _amount(line.net_amount),
        currencyID=currency,
    )

    item = _el(node, CAC, "Item")
    # Description (BT-154, optional) before Name (BT-153, mandatory): UBL's
    # sequence, and the XSD rejects the intuitive order.
    if line.description is not None:
        _el(item, CBC, "Description", line.description)
    _el(item, CBC, "Name", line.name)
    # No exemption reason here: UBL-CR-601 (a warning) says it should not
    # appear at line level.
    _tax_category(item, "ClassifiedTaxCategory", line.vat_category, line.vat_rate)

    price = _el(node, CAC, "Price")
    _el(price, CBC, "PriceAmount", _price(line.unit_price), currencyID=currency)


def to_ubl(invoice: Invoice) -> bytes:
    """Render an invoice as EN16931 UBL 2.1 XML."""
    root = etree.Element(f"{{{INVOICE_NS}}}Invoice", nsmap=NSMAP)

    _el(root, CBC, "CustomizationID", CUSTOMIZATION_ID)
    _el(root, CBC, "ID", invoice.number)
    _el(root, CBC, "IssueDate", invoice.issue_date.isoformat())
    if invoice.due_date is not None:
        _el(root, CBC, "DueDate", invoice.due_date.isoformat())
    _el(root, CBC, "InvoiceTypeCode", invoice.type_code.value)
    _el(root, CBC, "DocumentCurrencyCode", invoice.currency)

    _party(root, "AccountingSupplierParty", invoice.seller)
    _party(root, "AccountingCustomerParty", invoice.buyer)

    for item in invoice.allowance_charges:
        _allowance_charge(root, item, invoice.currency)

    _tax_total(root, invoice)
    _monetary_total(root, invoice)

    for line in invoice.lines:
        _invoice_line(root, line, invoice.currency)

    return etree.tostring(
        root, pretty_print=True, xml_declaration=True, encoding="UTF-8"
    )
