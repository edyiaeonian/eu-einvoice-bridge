from enum import StrEnum

from pydantic import BaseModel, ConfigDict, StrictBool


class ExemptionBasis(StrEnum):
    """Which kind of provision an exemption rests on, selecting P_19A/B/C.

    The provision's text itself comes from the lines' exemption reason (BT-120);
    FA(3) additionally wants to know whether it cites Polish law, the VAT
    Directive, or some other basis.
    """

    POLISH_ACT = "polish_act"  # P_19A
    EU_DIRECTIVE = "eu_directive"  # P_19B
    OTHER = "other"  # P_19C


class PolishExtras(BaseModel):
    """Statutory declarations FA(3) requires and EN16931 has no place for.

    None has a default. A default of "no" can yield an invoice that passes the
    schema and KSeF and is still legally wrong -- split payment is compulsory
    above a threshold for certain goods, and no validator would notice it
    missing. So the block is optional on the invoice, since UBL does not need
    it, but complete whenever it is given.

    Strict booleans only: FA(3) itself writes yes/no as "1"/"2", and accepting
    strings would invite "2" copied across meaning no.

    What can be derived from the invoice is not asked for here: reverse charge
    (P_18) follows from AE lines, the exemption flag (P_19/P_19N) from E lines,
    and the invoice kind from the type code.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    buyer_local_government_unit: StrictBool  # Podmiot2/JST
    buyer_vat_group_member: StrictBool  # Podmiot2/GV
    cash_accounting: StrictBool  # Adnotacje/P_16
    self_billing: StrictBool  # Adnotacje/P_17
    split_payment: StrictBool  # Adnotacje/P_18A
    simplified_triangular_procedure: StrictBool  # Adnotacje/P_23
    margin_scheme: StrictBool  # Adnotacje/PMarzy
    intra_eu_new_means_of_transport: StrictBool  # Adnotacje/NoweSrodkiTransportu
    exemption_basis: ExemptionBasis | None = None
