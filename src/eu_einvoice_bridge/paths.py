from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

ASSETS_DIR = _REPO_ROOT / "assets"
EN16931_DIR = ASSETS_DIR / "en16931"
EN16931_XSLT = EN16931_DIR / "xslt" / "EN16931-UBL-validation.xslt"
EN16931_EXAMPLES = EN16931_DIR / "examples"
