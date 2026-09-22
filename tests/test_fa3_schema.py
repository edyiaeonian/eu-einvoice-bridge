"""The official FA(3) schema, loaded and applied entirely offline.

No machine-readable official FA(3) sample invoice could be found, so the
fixture here is hand-built. That makes the negative cases essential: a schema
that accepted everything would make a hand-built fixture pass just as easily.
"""

from pathlib import Path

import pytest
from lxml import etree

from eu_einvoice_bridge.paths import FA3_XSD
from eu_einvoice_bridge.validate import Severity, validate_fa3
from eu_einvoice_bridge.validate.fa3 import CRD_BASE_PREFIX, fa3_schema

FIXTURE = Path(__file__).parent / "fixtures" / "fa3_minimal.xml"
NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"


def minimal() -> etree._Element:
    return etree.fromstring(FIXTURE.read_bytes())


def serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


class TestLoadingIsOffline:
    def test_the_schema_imports_its_base_types_by_absolute_url(self):
        # The reason a resolver is needed at all.
        text = FA3_XSD.read_text(encoding="utf-8")
        assert f'schemaLocation="{CRD_BASE_PREFIX}StrukturyDanych_v10-0E.xsd"' in text

    def test_without_the_local_resolver_the_import_cannot_be_resolved(self):
        # Proves nothing is being fetched over the network behind our back:
        # with networking disabled and no mapping, loading must fail.
        parser = etree.XMLParser(no_network=True)
        with pytest.raises(etree.XMLSchemaParseError):
            etree.XMLSchema(etree.parse(str(FA3_XSD), parser))

    def test_with_the_resolver_it_loads(self):
        assert fa3_schema() is not None


class TestTheMinimalFixture:
    def test_passes_the_official_schema(self):
        assert validate_fa3(FIXTURE.read_bytes()) == []


class TestTheSchemaActuallyRejects:
    """Without these, the fixture passing would prove nothing."""

    def test_a_missing_mandatory_element(self):
        # JST is mandatory in FA(3) even though it is almost always "2".
        root = minimal()
        jst = root.find(f"{{{NS}}}Podmiot2/{{{NS}}}JST")
        jst.getparent().remove(jst)
        issues = validate_fa3(serialize(root))
        assert issues and all(i.severity is Severity.ERROR for i in issues)

    def test_elements_out_of_sequence(self):
        root = minimal()
        fa = root.find(f"{{{NS}}}Fa")
        p1, p2 = fa.find(f"{{{NS}}}P_1"), fa.find(f"{{{NS}}}P_2")
        fa.remove(p1)
        p2.addnext(p1)
        assert validate_fa3(serialize(root))

    def test_a_rate_code_poland_does_not_have(self):
        # EN16931 allows any rate; FA(3)'s P_12 is a closed list.
        root = minimal()
        root.find(f".//{{{NS}}}FaWiersz/{{{NS}}}P_12").text = "19"
        assert validate_fa3(serialize(root))

    def test_a_malformed_nip(self):
        root = minimal()
        root.find(f".//{{{NS}}}Podmiot1//{{{NS}}}NIP").text = "PL5555555555"
        assert validate_fa3(serialize(root))


class TestUnreadableInput:
    def test_is_reported_rather_than_raised(self):
        issues = validate_fa3(b"<Faktura>not closed")
        assert [i.source for i in issues] == ["xml"]
