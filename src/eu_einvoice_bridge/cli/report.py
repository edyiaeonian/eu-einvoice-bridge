"""Turn validation issues into the report a person reads.

This project has no user interface, so the report is the product surface. It is
built on one rule: never make someone run the command twice to learn about a
second problem.
"""

from itertools import groupby

from pydantic import ValidationError

from ..validate import Severity, ValidationIssue

LAYER_NAMES = {
    "json": "input",
    "model": "model",
    "xml": "xml",
    "xsd": "schema",
    "schematron": "business rules",
}


def issues_from_pydantic(error: ValidationError) -> list[ValidationIssue]:
    """Adapt Pydantic's errors into the shape every other layer reports in.

    Pydantic already collects all of them, so the report inherits the
    all-at-once property here for free.
    """
    issues = []
    for entry in error.errors():
        location = ".".join(str(part) for part in entry["loc"])
        # Pydantic prefixes anything raised from a validator with "Value error,",
        # which says nothing the reader does not already know.
        message = entry["msg"].removeprefix("Value error, ")
        issues.append(
            ValidationIssue(
                source="model",
                severity=Severity.ERROR,
                message=message,
                location=location or None,
            )
        )
    return issues


def _format_issue(issue: ValidationIssue) -> list[str]:
    lines = []
    if issue.rule_id:
        heading = f"  {issue.rule_id}"
        if issue.business_terms:
            heading += f"  {', '.join(issue.business_terms)}"
        lines.append(heading)
        if issue.location:
            lines.append(f"    at {issue.location}")
        lines.append(f"    {issue.message}")
    else:
        where = issue.location or (
            f"line {issue.line}" if issue.line is not None else "document"
        )
        lines.append(f"  {where}")
        lines.append(f"    {issue.message}")
    return lines


def format_issues(source_name: str, issues: list[ValidationIssue]) -> str:
    """Group issues by the layer that found them, in the order they ran."""
    count = len(issues)
    noun = "problem" if count == 1 else "problems"
    out = [f"{source_name}: {count} {noun}", ""]

    for layer, group in groupby(issues, key=lambda i: i.source):
        found = list(group)
        errors = sum(1 for i in found if i.severity is Severity.ERROR)
        label = LAYER_NAMES.get(layer, layer)
        out.append(f"{label} ({errors} error{'s' if errors != 1 else ''})")
        for issue in found:
            out.extend(_format_issue(issue))
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def format_success(source_name: str, invoice) -> str:
    categories = {entry.category.value for entry in invoice.vat_breakdown}
    return (
        f"{source_name}: valid\n"
        f"  EN16931 (UBL 2.1), {len(invoice.lines)} line"
        f"{'s' if len(invoice.lines) != 1 else ''}, "
        f"VAT categories {', '.join(sorted(categories))}\n"
        f"  total {invoice.totals.total_with_vat} {invoice.currency}, "
        f"due {invoice.totals.amount_due} {invoice.currency}\n"
    )
