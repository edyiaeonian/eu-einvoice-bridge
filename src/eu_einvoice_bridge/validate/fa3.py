"""Validate FA(3) against the official schema, with no network access.

FA(3) has no Schematron layer to mirror EN16931's, so locally the XSD is the
whole check -- and it is the authoritative one.
"""

from functools import lru_cache

from lxml import etree

from ..paths import FA3_BASE_DIR, FA3_XSD
from .issues import Severity, ValidationIssue
from .validator import _parse

# The FA(3) schema imports its base types by absolute URL. The Ministry's own
# repository ships those base schemas alongside it, and they are canonically
# identical to what crd.gov.pl serves -- differing only in whitespace, comments
# and whether includes are written as absolute URLs or relative paths.
CRD_BASE_PREFIX = (
    "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/DefinicjeTypy/"
)


class _VendoredCrdResolver(etree.Resolver):
    """Serve the crd.gov.pl base schemas from the vendored copies.

    Mapping at load time leaves the official files unmodified. Anything outside
    the known prefix is left unresolved, and with networking disabled that makes
    loading fail rather than quietly reach out to the internet.
    """

    def resolve(self, url, public_id, context):
        if url.startswith(CRD_BASE_PREFIX):
            local = FA3_BASE_DIR / url[len(CRD_BASE_PREFIX) :]
            return self.resolve_filename(str(local), context)
        return None


@lru_cache(maxsize=1)
def fa3_schema() -> etree.XMLSchema:
    parser = etree.XMLParser(no_network=True)
    parser.resolvers.add(_VendoredCrdResolver())
    return etree.XMLSchema(etree.parse(str(FA3_XSD), parser))


def validate_fa3(xml: bytes) -> list[ValidationIssue]:
    """Every schema violation in an FA(3) document, not only the first."""
    tree, syntax_error = _parse(xml)
    if syntax_error is not None:
        return [syntax_error]

    schema = fa3_schema()
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
