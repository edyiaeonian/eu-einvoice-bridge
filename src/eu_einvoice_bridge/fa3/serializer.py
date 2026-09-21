"""Serialize the neutral model to Poland's FA(3).

Element order is fixed by the schema's sequences and follows the vendored XSD.
The same never-rounding formatters as the UBL serializer render every number.
"""

from datetime import datetime, timezone
from decimal import Decimal

from lxml import etree

from ..formatting import amount, price, quantity
from ..model import ExemptionBasis, Invoice, LineItem, Party, PolishExtras, VatCategory
from ..validate.issues import Severity, ValidationIssue
from .checks import fa3_issues
from .mapping import BUCKET_ORDER, FA3_NS, eu_vat_prefixes, rate_slot

_BASIS_ELEMENT = {
    ExemptionBasis.POLISH_ACT: "P_19A",
    ExemptionBasis.EU_DIRECTIVE: "P_19B",
    ExemptionBasis.OTHER: "P_19C",
}


class Fa3MappingError(ValueError):
    """The invoice holds something FA(3) cannot express faithfully.

    Carries every blocking issue, not only the first, so a caller can report
    them all in one pass.
    """

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{i.rule_id}: {i.message}" for i in self.issues))


def _el(parent, name: str, text: str | None = None, **attrs):
    node = etree.SubElement(parent, f"{{{FA3_NS}}}{name}", **attrs)
    if text is not None:
        node.text = text
    return node


def _yes_no(flag: bool) -> str:
    return "1" if flag else "2"


def _header(root, generated_at: datetime) -> None:
    header = _el(root, "Naglowek")
    _el(header, "KodFormularza", "FA", kodSystemowy="FA (3)", wersjaSchemy="1-0E")
    _el(header, "WariantFormularza", "3")
    stamp = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _el(header, "DataWytworzeniaFa", stamp)


def _address(parent, party: Party) -> None:
    # FA(3) takes free-text address lines, not the structured parts EN16931 has.
    address = _el(parent, "Adres")
    _el(address, "KodKraju", party.address.country)
    _el(address, "AdresL1", party.address.street)
    _el(address, "AdresL2", f"{party.address.postal_code} {party.address.city}")


def _seller(root, party: Party) -> None:
    seller = _el(root, "Podmiot1")
    ids = _el(seller, "DaneIdentyfikacyjne")
    _el(ids, "NIP", party.vat_id[2:])
    _el(ids, "Nazwa", party.name)
    _address(seller, party)


def _buyer(root, party: Party, extras: PolishExtras) -> None:
    buyer = _el(root, "Podmiot2")
    ids = _el(buyer, "DaneIdentyfikacyjne")
    vat_id = party.vat_id
    if vat_id is None:
        _el(ids, "BrakID", "1")
    elif vat_id.startswith("PL"):
        _el(ids, "NIP", vat_id[2:])
    elif vat_id[:2] in eu_vat_prefixes():
        # The code is the VAT prefix, which is not always the ISO country code.
        _el(ids, "KodUE", vat_id[:2])
        _el(ids, "NrVatUE", vat_id[2:])
    else:
        _el(ids, "KodKraju", party.address.country)
        _el(ids, "NrID", vat_id)
    _el(ids, "Nazwa", party.name)
    _address(buyer, party)
    _el(buyer, "JST", _yes_no(extras.buyer_local_government_unit))
    _el(buyer, "GV", _yes_no(extras.buyer_vat_group_member))


def _buckets(invoice: Invoice) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Net and tax per FA(3) slot. Distinct rates can share one (23% and 22%)."""
    net: dict[str, Decimal] = {}
    tax: dict[str, Decimal] = {}
    for entry in invoice.vat_breakdown:
        slot = rate_slot(entry.category, entry.rate)
        net[slot.bucket] = net.get(slot.bucket, Decimal("0")) + entry.taxable_amount
        if slot.taxed:
            tax[slot.bucket] = tax.get(slot.bucket, Decimal("0")) + entry.tax_amount
    return net, tax


def _annotations(fa, invoice: Invoice, extras: PolishExtras) -> None:
    notes = _el(fa, "Adnotacje")
    _el(notes, "P_16", _yes_no(extras.cash_accounting))
    _el(notes, "P_17", _yes_no(extras.self_billing))
    reverse_charge = any(
        line.vat_category is VatCategory.REVERSE_CHARGE for line in invoice.lines
    )
    _el(notes, "P_18", _yes_no(reverse_charge))
    _el(notes, "P_18A", _yes_no(extras.split_payment))

    exemption = _el(notes, "Zwolnienie")
    exempt = next(
        (e for e in invoice.vat_breakdown if e.category is VatCategory.EXEMPT), None
    )
    if exempt is None:
        _el(exemption, "P_19N", "1")
    else:
        _el(exemption, "P_19", "1")
        _el(exemption, _BASIS_ELEMENT[extras.exemption_basis], exempt.exemption_reason)

    transport = _el(notes, "NoweSrodkiTransportu")
    _el(transport, "P_22N", "1")

    _el(notes, "P_23", _yes_no(extras.simplified_triangular_procedure))

    margin = _el(notes, "PMarzy")
    _el(margin, "P_PMarzyN", "1")


def _line(fa, position: int, line: LineItem) -> None:
    row = _el(fa, "FaWiersz")
    # NrWierszaFa must be a positive integer; BT-126 may be any text.
    _el(row, "NrWierszaFa", str(position))
    _el(row, "P_7", line.name)
    _el(row, "P_8A", line.unit_code)
    _el(row, "P_8B", quantity(line.quantity))
    _el(row, "P_9A", price(line.unit_price))
    _el(row, "P_11", amount(line.net_amount))
    _el(row, "P_12", rate_slot(line.vat_category, line.vat_rate).code)


def _fa(root, invoice: Invoice, extras: PolishExtras) -> None:
    fa = _el(root, "Fa")
    _el(fa, "KodWaluty", invoice.currency)
    _el(fa, "P_1", invoice.issue_date.isoformat())
    _el(fa, "P_2", invoice.number)

    net, tax = _buckets(invoice)
    for bucket in BUCKET_ORDER:
        if bucket in net:
            _el(fa, f"P_13_{bucket}", amount(net[bucket]))
            if bucket in tax:
                _el(fa, f"P_14_{bucket}", amount(tax[bucket]))

    _el(fa, "P_15", amount(invoice.totals.total_with_vat))
    _annotations(fa, invoice, extras)
    _el(fa, "RodzajFaktury", "VAT")

    # BT-154 has no field of its own; DodatkowyOpis is the schema's place for
    # "additional data for which no other field is provided".
    for position, line in enumerate(invoice.lines, start=1):
        if line.description is not None:
            extra = _el(fa, "DodatkowyOpis")
            _el(extra, "NrWiersza", str(position))
            _el(extra, "Klucz", "Opis")
            _el(extra, "Wartosc", line.description)

    for position, line in enumerate(invoice.lines, start=1):
        _line(fa, position, line)

    if invoice.prepaid_amount:
        # A prepayment leaves the tax base alone, which is exactly what
        # Rozliczenie adjusts: the payable amount, not P_13/P_14.
        settlement = _el(fa, "Rozliczenie")
        deduction = _el(settlement, "Odliczenia")
        _el(deduction, "Kwota", amount(invoice.prepaid_amount))
        _el(deduction, "Powod", "Przedpłata")
        _el(settlement, "SumaOdliczen", amount(invoice.prepaid_amount))
        _el(settlement, "DoZaplaty", amount(invoice.totals.amount_due))

    if invoice.due_date is not None:
        payment = _el(fa, "Platnosc")
        term = _el(payment, "TerminPlatnosci")
        _el(term, "Termin", invoice.due_date.isoformat())


def to_fa3(invoice: Invoice, *, generated_at: datetime) -> bytes:
    """Render an invoice as FA(3) XML, or raise with every blocking issue.

    generated_at fills DataWytworzeniaFa. It is passed in rather than read from
    the clock so the same invoice always renders to the same bytes.
    """
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")

    # Everything below assumes a mappable invoice; this is what makes it so.
    # Warnings do not block and are the caller's to report via fa3_issues().
    blocking = [i for i in fa3_issues(invoice) if i.severity is Severity.ERROR]
    if blocking:
        raise Fa3MappingError(blocking)

    root = etree.Element(f"{{{FA3_NS}}}Faktura", nsmap={None: FA3_NS})
    _header(root, generated_at)
    _seller(root, invoice.seller)
    _buyer(root, invoice.buyer, invoice.extras)
    _fa(root, invoice, invoice.extras)
    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
