"""Run a UBL invoice through the validation chain and report what fails.

Cheapest layer first: a document that is not well-formed cannot be schema
checked, and one that fails the schema will usually produce misleading semantic
complaints, so each layer only runs if the previous one was clean.
"""

import re
from functools import lru_cache

from lxml import etree
from saxonche import PySaxonProcessor

from ..paths import EN16931_XSLT, UBL_INVOICE_XSD
from .issues import Severity, ValidationIssue

SVRL_NS = {"svrl": "http://purl.oclc.org/dsdl/svrl"}

# Rule texts name the fields they constrain as "(BT-112)" or "(BG-23)"; pulling
# them out lets the report point at a field rather than only at a rule number.
_TERM = re.compile(r"\b(B[TG]-\d+)\b")

# SVRL repeats the rule id at the head of its own message: "[BR-CO-15]-Invoice
# total...". The id is reported separately, so the prefix is redundant.
_RULE_PREFIX = re.compile(r"^\[[A-Z0-9-]+\]\s*-?\s*")

# SVRL locations are namespace-literal XPath, unreadable at a glance:
#   /*:Invoice[namespace-uri()='urn:...Invoice-2'][1]/*:LegalMonetaryTotal[...]
_LOCATION_STEP = re.compile(
    r"\*:([A-Za-z0-9_.-]+)\[namespace-uri\(\)='([^']*)'\](?:\[(\d+)\])?"
)

_PREFIXES = {
    "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2": "cac",
    "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2": "cbc",
}


def _readable_location(location: str | None) -> str | None:
    """Turn SVRL's namespace-literal XPath into something a person can follow.

    Positional indexes are kept only when they are not [1]: on a repeated
    element such as an invoice line the index is the whole point, while on a
    singleton it is noise.
    """
    if not location:
        return None

    def step(match: re.Match[str]) -> str:
        name, namespace, index = match.groups()
        prefix = _PREFIXES.get(namespace)
        rendered = f"{prefix}:{name}" if prefix else name
        return f"{rendered}[{index}]" if index and index != "1" else rendered

    return _LOCATION_STEP.sub(step, location)


@lru_cache(maxsize=1)
def _xsd() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(UBL_INVOICE_XSD)))


def _schematron_svrl(xml: bytes) -> str:
    # Saxon reads from a file or a string; a string avoids a temp file and the
    # cleanup that would come with it.
    with PySaxonProcessor(license=False) as proc:
        executable = proc.new_xslt30_processor().compile_stylesheet(
            stylesheet_file=str(EN16931_XSLT)
        )
        document = proc.parse_xml(xml_text=xml.decode("utf-8"))
        return executable.transform_to_string(xdm_node=document)


def _parse(xml: bytes) -> tuple[etree._ElementTree | None, ValidationIssue | None]:
    try:
        return etree.ElementTree(etree.fromstring(xml)), None
    except etree.XMLSyntaxError as exc:
        return None, ValidationIssue(
            source="xml",
            severity=Severity.ERROR,
            message=str(exc),
            line=getattr(exc, "lineno", None),
        )


def _xsd_issues(tree: etree._ElementTree) -> list[ValidationIssue]:
    schema = _xsd()
    if schema.validate(tree):
        return []
    return [
        ValidationIssue(
            source="xsd",
            severity=Severity.ERROR,
            message=entry.message,
            line=entry.line,
        )
        for entry in schema.error_log
    ]


# Every assertion in the official rules carries a flag. Of the 979 in the
# vendored XSLT, 281 are fatal and 698 are warnings -- the whole UBL-CR series
# among them -- so treating every failure as fatal would reject invoices the
# standard accepts.
_FLAG_SEVERITY = {"fatal": Severity.ERROR, "warning": Severity.WARNING}


def _schematron_issues(xml: bytes) -> list[ValidationIssue]:
    svrl = etree.fromstring(_schematron_svrl(xml).encode("utf-8"))
    issues = []
    for tag, unflagged in (
        # A failed assertion without a flag is assumed fatal: the cautious
        # reading, since the alternative is waving through a real violation.
        ("failed-assert", Severity.ERROR),
        ("successful-report", Severity.WARNING),
    ):
        for node in svrl.findall(f".//svrl:{tag}", SVRL_NS):
            raw = " ".join(
                (node.findtext("svrl:text", namespaces=SVRL_NS) or "").split()
            )
            issues.append(
                ValidationIssue(
                    source="schematron",
                    severity=_FLAG_SEVERITY.get(node.get("flag"), unflagged),
                    rule_id=node.get("id"),
                    business_terms=tuple(dict.fromkeys(_TERM.findall(raw))),
                    location=_readable_location(node.get("location")),
                    message=_RULE_PREFIX.sub("", raw),
                )
            )
    return issues


def validate_ubl(xml: bytes) -> list[ValidationIssue]:
    """Validate a UBL invoice, returning every issue found rather than the first.

    Reporting one problem at a time turns fixing an invoice into a series of
    round trips, so each layer reports everything it sees before the chain
    stops.
    """
    tree, syntax_error = _parse(xml)
    if syntax_error is not None:
        return [syntax_error]

    issues = _xsd_issues(tree)
    if issues:
        # Semantic rules applied to a structurally invalid document produce
        # failures that describe the damage rather than its cause.
        return sorted(issues, key=lambda i: i.sort_key)

    issues += _schematron_issues(xml)
    return sorted(issues, key=lambda i: i.sort_key)
