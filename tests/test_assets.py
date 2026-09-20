"""Guards the vendored validation assets.

A missing or unreadable XSLT would make every downstream validation silently
pass, so the checkout is verified before anything relies on it.
"""

from saxonche import PySaxonProcessor

from eu_einvoice_bridge.paths import EN16931_EXAMPLES, EN16931_XSLT


def test_en16931_xslt_is_vendored():
    assert EN16931_XSLT.is_file(), f"缺少驗證資產:{EN16931_XSLT}"


def test_official_examples_are_vendored():
    examples = list(EN16931_EXAMPLES.glob("*.xml"))
    assert examples, f"缺少官方範例:{EN16931_EXAMPLES}"


def test_xslt_compiles_and_validates_a_conformant_example():
    example = EN16931_EXAMPLES / "ubl-tc434-example1.xml"

    with PySaxonProcessor(license=False) as proc:
        executable = proc.new_xslt30_processor().compile_stylesheet(
            stylesheet_file=str(EN16931_XSLT)
        )
        svrl = executable.transform_to_string(source_file=str(example))

    assert "schematron-output" in svrl
    assert "failed-assert" not in svrl
