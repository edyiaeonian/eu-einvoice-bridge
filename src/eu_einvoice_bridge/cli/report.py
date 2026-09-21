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


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _tally(issues: list[ValidationIssue], error_noun: str) -> str:
    errors = sum(1 for i in issues if i.severity is Severity.ERROR)
    warnings = len(issues) - errors
    parts = []
    if errors:
        parts.append(_plural(errors, error_noun))
    if warnings:
        parts.append(_plural(warnings, "warning"))
    return ", ".join(parts)


def _format_issue(issue: ValidationIssue) -> list[str]:
    # Warnings are marked; errors are the default and left unmarked, so the eye
    # lands on the entries that actually block the invoice.
    marker = "  (warning)" if issue.severity is Severity.WARNING else ""
    lines = []
    if issue.rule_id:
        heading = f"  {issue.rule_id}{marker}"
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
        lines.append(f"  {where}{marker}")
        lines.append(f"    {issue.message}")
    return lines


def _format_layers(issues: list[ValidationIssue]) -> list[str]:
    """Group issues by the layer that found them, in the order they ran."""
    out = []
    for layer, group in groupby(issues, key=lambda i: i.source):
        found = list(group)
        out.append(f"{LAYER_NAMES.get(layer, layer)} ({_tally(found, 'error')})")
        for issue in found:
            out.extend(_format_issue(issue))
        out.append("")
    return out


def format_issues(source_name: str, issues: list[ValidationIssue]) -> str:
    out = [f"{source_name}: {_tally(issues, 'problem')}", ""]
    out.extend(_format_layers(issues))
    return "\n".join(out).rstrip() + "\n"


def format_success(
    source_name: str, invoice, warnings: list[ValidationIssue] = ()
) -> str:
    categories = {entry.category.value for entry in invoice.vat_breakdown}
    heading = f"{source_name}: valid"
    if warnings:
        heading += f", {_plural(len(warnings), 'warning')}"
    out = [
        heading,
        f"  EN16931 (UBL 2.1), {_plural(len(invoice.lines), 'line')}, "
        f"VAT categories {', '.join(sorted(categories))}",
        f"  total {invoice.totals.total_with_vat} {invoice.currency}, "
        f"due {invoice.totals.amount_due} {invoice.currency}",
    ]
    if warnings:
        out.append("")
        out.extend(_format_layers(list(warnings)))
    return "\n".join(out).rstrip() + "\n"
