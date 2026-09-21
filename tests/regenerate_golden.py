"""Rewrite the golden baseline.

Run this only when the output is *meant* to change, and read the diff before
committing it — the baseline exists to make unintended changes visible, so
regenerating it on a whim defeats the point.

    python -m tests.regenerate_golden
"""

from eu_einvoice_bridge.fa3 import to_fa3
from eu_einvoice_bridge.ubl import to_ubl

from .test_fa3 import GENERATED_AT, GOLDEN_FA3, a_golden_fa3_invoice
from .test_ubl import GOLDEN, a_golden_invoice

if __name__ == "__main__":
    GOLDEN.write_bytes(to_ubl(a_golden_invoice()))
    print(f"rewrote {GOLDEN}")
    GOLDEN_FA3.write_bytes(to_fa3(a_golden_fa3_invoice(), generated_at=GENERATED_AT))
    print(f"rewrote {GOLDEN_FA3}")
