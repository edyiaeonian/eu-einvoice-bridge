from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

ASSETS_DIR = _REPO_ROOT / "assets"
EN16931_DIR = ASSETS_DIR / "en16931"
EN16931_XSLT = EN16931_DIR / "xslt" / "EN16931-UBL-validation.xslt"
EN16931_EXAMPLES = EN16931_DIR / "examples"
UBL21_DIR = ASSETS_DIR / "ubl21"
UBL_INVOICE_XSD = UBL21_DIR / "xsd" / "maindoc" / "UBL-Invoice-2.1.xsd"
FA3_DIR = ASSETS_DIR / "fa3"
FA3_XSD = FA3_DIR / "schemat_FA(3)_v1-0E.xsd"
FA3_BASE_DIR = FA3_DIR / "bazowe"
