"""Rewrite the golden baseline.

Run this only when the output is *meant* to change, and read the diff before
committing it — the baseline exists to make unintended changes visible, so
regenerating it on a whim defeats the point.

    python -m tests.regenerate_golden
"""

from eu_einvoice_bridge.ubl import to_ubl

from .test_ubl import GOLDEN, a_golden_invoice

if __name__ == "__main__":
    GOLDEN.write_bytes(to_ubl(a_golden_invoice()))
    print(f"rewrote {GOLDEN}")
