from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from lxml import etree


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


# Ordered by the stage that found them. Mapping problems exist before any XML
# does; after that, a structural problem is the likelier root cause, and a
# semantic rule firing on a malformed document is usually noise.
SOURCE_ORDER = {"mapping": 0, "xml": 1, "xsd": 2, "schematron": 3}

SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1}


def has_errors(issues: Iterable["ValidationIssue"]) -> bool:
    """Whether anything found makes the invoice unacceptable.

    Warnings are reported but never decide the outcome; that is what separates
    them from errors in the official rules.
    """
    return any(issue.severity is Severity.ERROR for issue in issues)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One problem, from whichever layer found it.

    Both layers produce this same shape so a caller never has to know which
    layer complained in order to display the result.
    """

    source: str
    severity: Severity
    message: str
    rule_id: str | None = None
    business_terms: tuple[str, ...] = ()
    location: str | None = None
    line: int | None = None

    @property
    def sort_key(self) -> tuple[int, int, str]:
        return (
            SOURCE_ORDER.get(self.source, 99),
            SEVERITY_ORDER[self.severity],
            self.rule_id or "",
        )

    def __str__(self) -> str:
        label = self.rule_id or self.source.upper()
        terms = f" [{', '.join(self.business_terms)}]" if self.business_terms else ""
        where = ""
        if self.line is not None:
            where = f" (line {self.line})"
        elif self.location:
            where = f" ({self.location})"
        return f"{self.severity.value}: {label}{terms}{where} — {self.message}"


def parse_xml(xml: bytes) -> tuple[etree._ElementTree | None, ValidationIssue | None]:
    """Parse, or say why not: a syntax error is reported like any other issue.

    Shared by both validators, which each start from bytes.
    """
    try:
        return etree.ElementTree(etree.fromstring(xml)), None
    except etree.XMLSyntaxError as exc:
        return None, ValidationIssue(
            source="xml",
            severity=Severity.ERROR,
            message=str(exc),
            line=getattr(exc, "lineno", None),
        )
