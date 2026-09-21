from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


# Structural problems are the likelier root cause, and a semantic rule firing on
# a malformed document is usually noise, so they are reported first.
SOURCE_ORDER = {"xml": 0, "xsd": 1, "schematron": 2}


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
    def sort_key(self) -> tuple[int, str]:
        return (SOURCE_ORDER.get(self.source, 99), self.rule_id or "")

    def __str__(self) -> str:
        label = self.rule_id or self.source.upper()
        terms = f" [{', '.join(self.business_terms)}]" if self.business_terms else ""
        where = ""
        if self.line is not None:
            where = f" (line {self.line})"
        elif self.location:
            where = f" ({self.location})"
        return f"{self.severity.value}: {label}{terms}{where} — {self.message}"
