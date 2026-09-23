from pathlib import Path

# Inside the package, so a wheel carries them: a path relative to the repo root
# only resolves in an editable install, where the package is the checkout.
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
EN16931_DIR = ASSETS_DIR / "en16931"
EN16931_XSLT = EN16931_DIR / "xslt" / "EN16931-UBL-validation.xslt"
EN16931_EXAMPLES = EN16931_DIR / "examples"
UBL21_DIR = ASSETS_DIR / "ubl21"
UBL_INVOICE_XSD = UBL21_DIR / "xsd" / "maindoc" / "UBL-Invoice-2.1.xsd"
FA3_DIR = ASSETS_DIR / "fa3"
FA3_XSD = FA3_DIR / "schemat_FA(3)_v1-0E.xsd"
FA3_BASE_DIR = FA3_DIR / "bazowe"
