# EU E-Invoice Bridge

Turning one neutral invoice into two things at once: an **EN16931** invoice in UBL 2.1
syntax, and the **FA(3)** XML that Poland's KSeF requires.

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Neutral model, UBL serializer, validation chain, CLI error report | 🚧 in progress |
| 2 | FA(3) serializer, mismatch handling | planned |
| 3 | Encryption, KSeF client, UPO retrieval | planned |

The architecture below describes the finished shape. Modules from later phases are not
in the tree yet. See [the phase 1 plan](docs/plans/2026-09-20-phase1-implementation-plan.md).

---

## Why this exists

The EU set a semantic standard for e-invoices, EN16931. It says what an invoice must
*mean* — which parties, which tax categories, which totals. It does not say what file
a tax authority will accept.

For standard VAT invoices, Poland's KSeF accepts one syntax: its own national schema,
FA(3). And FA(3) is **not** a one-to-one mapping of EN16931. Some EN16931 business
terms have no Polish equivalent. Poland requires fields the European standard never
defined. An invoice in a foreign currency still has to report its tax in PLN, so FA(3)
carries an exchange rate that EN16931 expresses a different way entirely.

Every country running a CTC (continuous transaction control) regime has this problem,
and there are a lot of them now — Hungary since 2018, Italy since 2019, Poland from
2026, with France next. (Germany's 2025–2028 mandate is e-invoicing without clearance,
and its formats are EN16931 CIUSes, so it is a different problem.) The EU's ViDA
reform makes EN16931 mandatory for cross-border trade in 2030 without making the
national formats go away.

**So the interesting engineering question is not "can you call the API". It is: where
does the gap between the standard and the national format live in your code?**

This project's answer: **not in a conversion function.** Converting EN16931 directly
to FA(3) collapses into a pile of special cases, and the gap disappears into it. Here
both formats are produced from one neutral model by two independent serializers, so
each difference stays visible in the one place that owns it.

### The four kinds of mismatch

Each gets a deliberate answer, and they are not the same answer:

| Case | Handling |
|---|---|
| Both standards have the field | Map directly |
| Poland-only, absent from EN16931 | Lives in a named `PolishExtras` model, kept out of the core |
| EN16931 has it, FA(3) does not | Dropped on the Polish path — but **reported, never silently** |
| **FA(3) requires it, the neutral model has no field** | **Fail before emitting.** A file certain to be rejected is not worth sending |

The third and fourth cases pull in opposite directions, which is the point. Dropping a
field the other side cannot hold is survivable if you say so. Emitting a file you
already know is invalid just spends a submission to learn nothing.

**A worked example — foreign currency.** Invoice currency (BT-5) exists on both sides,
so it maps directly. But Poland wants the tax reported in PLN regardless, so FA(3)
carries an exchange rate field (`KursWaluty`) that EN16931 has no slot for, while
EN16931 expresses the same requirement as a separate VAT accounting currency (BT-6).
One requirement, two unrelated shapes: case 1 and case 2 in the same field.

## Architecture

```
                  ①  model              neutral, knows no country
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
      ② ubl                   ④ fa3        serialize
          │                       │
          ▼                       ▼
      ③ validate              ③ validate   EN16931 Schematron + XSD  /  FA(3) XSD
                                  │
                                  ▼
                              ⑤ crypto     AES-256-CBC + RSA-OAEP
                                  │
                                  ▼
                              ⑥ ksef       submit, poll, fetch UPO

                  ⑦  cli
```

Both paths are validated locally before anything leaves the machine — the sandbox is
never the first validator. Only the FA(3) path continues; the UBL output exists to
prove conformance to the European standard, not to be submitted anywhere.

Serializing and *doing something with the result* are separate modules on purpose:
they fail for unrelated reasons — bad input data versus a sandbox that is down — and
splitting them keeps serialization testable with no network at all.

`crypto` is separate from `ksef` for the same reason. Encryption is a pure function,
so it can be verified offline against known test vectors, and an encryption bug never
looks like a connection bug.

## Quick start

Requires Python 3.12+.

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/pytest
```

Tests run fully offline, including the Schematron validation, because the official
validation artefacts are vendored rather than fetched.

Tests that need Poland's sandbox are marked `integration` and excluded by default, so
a sandbox outage never turns the suite red. They arrive in phase 3, along with the
credentials setup they require:

```bash
./.venv/bin/pytest -m integration    # phase 3
```

### Troubleshooting: editable install on macOS with Python 3.14

If `import eu_einvoice_bridge` fails outside pytest even though `pip list` shows the
package installed, check the flags on its `.pth` file:

```bash
ls -lO .venv/lib/python3.14/site-packages/_editable_impl_eu_einvoice_bridge.pth
```

macOS marks files inside a venv with the BSD `UF_HIDDEN` flag, and Python 3.14's
`site.py` skips hidden `.pth` files — silently, with no error anywhere. Clear it:

```bash
chflags nohidden .venv/lib/python3.14/site-packages/*.pth
```

The test suite is unaffected: `pythonpath = ["src"]` in `pyproject.toml` puts the
source tree on `sys.path` directly, so tests never depend on the editable install
taking effect.

## Documentation

| Document | Contents |
|---|---|
| [Design spec](docs/specs/2026-09-20-ksef-en16931-bridge-design.md) | Architecture, data model, the seven design decisions, error handling, test strategy |
| [Known limitations](docs/specs/2026-09-20-ksef-en16931-bridge-design.md#14-已知限制) | What this deliberately does not do |
| [Background](docs/BACKGROUND.md) | ViDA and national mandates, why Poland rather than Hungary, first-party sources |
| [Phase 1 plan](docs/plans/2026-09-20-phase1-implementation-plan.md) | Step-by-step plan and acceptance criteria |

The design documents are written in Traditional Chinese; the spec's structure and the
BT/BR identifiers throughout are language-independent.

## Third-party assets

`assets/` holds the official EN16931 validation artefacts, vendored unmodified from
[ConnectingEurope/eInvoicing-EN16931](https://github.com/ConnectingEurope/eInvoicing-EN16931)
at release `validation-1.3.16`. They are licensed under the **EUPL v1.2**, separately
from this repository's own code.

See [`assets/README.md`](assets/README.md) for provenance and
[`assets/en16931/LICENSE.txt`](assets/en16931/LICENSE.txt) for the licence text.

## Licence

Source code: MIT — see [LICENSE](LICENSE). Vendored assets: EUPL v1.2, as above.
