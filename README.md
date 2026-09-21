# EU E-Invoice Bridge

[![CI](https://github.com/edyiaeonian/eu-einvoice-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/edyiaeonian/eu-einvoice-bridge/actions/workflows/ci.yml)

Turning one neutral invoice into two things at once: an **EN16931** invoice in UBL 2.1
syntax, and the **FA(3)** XML that Poland's KSeF requires.

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Neutral model, UBL serializer, validation chain, CLI error report | ✅ complete |
| 2 | FA(3) serializer, mismatch handling | planned |
| 3 | Encryption, KSeF client, UPO retrieval | planned |

The architecture below describes the finished shape; `crypto` and `ksef` arrive in
phase 3. See [the phase 1 plan](docs/plans/2026-09-20-phase1-implementation-plan.md).

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

This is phase 2's job — the FA(3) path does not exist yet. Each case gets a deliberate
answer, and they are not the same answer:

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

Phase 1 already applies the fourth rule to itself. BT-6 obliges an invoice to carry
BT-111, the VAT total in that second currency (BR-53, fatal), and that needs an
exchange rate the model does not have yet. So the model refuses BT-6 outright rather
than emit an invoice it knows will fail.

## What it looks like

With no UI, the error report is the product surface. It shows everything wrong at
once — never one problem per run — and each entry names the rule, the business
terms it constrains, and where to look.

Deleting one field, the seller's VAT identifier, breaks three separate rules:

```
$ einvoice validate invoice.json
invoice.json: 3 problems

business rules (3 errors)
  BR-AE-02  BG-25, BT-151, BT-31, BT-32, BT-63, BT-48, BT-47
    at /Invoice
    An Invoice that contains an Invoice line (BG-25) where the Invoiced item VAT
    category code (BT-151) is "Reverse charge" shall contain the Seller VAT
    Identifier (BT-31), ... and the Buyer VAT identifier (BT-48) ...
  BR-S-02  BG-25, BT-151, BT-31, BT-32, BT-63
    at /Invoice
    ...
  BR-S-03  BG-20, BT-95, BT-31, BT-32, BT-63
    at /Invoice
    ...
```

That invoice is accepted by the model — no single field is wrong. It is the
document as a whole that breaks the rules, which is the division of labour between
the two: the model refuses states that are meaningless on their own, Schematron
knows rules that span the whole invoice.

A valid one:

```
$ einvoice validate invoice.json
invoice.json: valid
  EN16931 (UBL 2.1), 2 lines, VAT categories AE, S
  total 710.70 PLN, due 610.70 PLN
```

Exit codes separate the two questions a caller has: `0` valid, `1` the invoice is
wrong, `2` the file could not be read at all.

Severity follows the flag on each official rule. Of the 979 assertions, 281 are fatal
and 698 are warnings; only fatal ones make an invoice invalid. Warnings are listed,
marked `(warning)`, and neither change the exit code nor stop `convert` from writing
the file.

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

./.venv/bin/einvoice validate examples/invoice.json
./.venv/bin/einvoice convert examples/invoice.json -o invoice.xml

# Poland's FA(3); the same input also converts to UBL
./.venv/bin/einvoice validate examples/invoice-fa3.json --format fa3
./.venv/bin/einvoice convert examples/invoice-fa3.json --format fa3 -o invoice-fa3.xml
```

`examples/invoice.json` is refused for FA(3), on purpose: it carries a document-level
allowance, which lowers the tax base in EN16931 and has no counterpart in FA(3), and
it lacks the Polish statutory declarations. The report names both.

Tests run fully offline, including the Schematron validation, because the official
validation artefacts are vendored rather than fetched.

Tests that need Poland's sandbox are marked `integration` and excluded by default, so
a sandbox outage never turns the suite red. They arrive in phase 3, along with the
credentials setup they require:

```bash
./.venv/bin/pytest -m integration    # phase 3
```

### Troubleshooting: keep the venv out of iCloud Drive

If the `einvoice` command fails with `ModuleNotFoundError` even though `pip list`
shows the package installed, the venv is probably inside an iCloud-synced folder —
on macOS, Desktop and Documents are synced by default.

Three things line up to produce a silent failure:

1. iCloud marks the `.venv` directory with the BSD `UF_HIDDEN` flag, and the flag
   spreads to files inside it
2. Python 3.14's `site.py` skips hidden `.pth` files
3. An editable install *is* a `.pth` file, so the package silently stops resolving

Nothing reports an error at any step. `chflags nohidden .venv/lib/*/site-packages/*.pth`
clears it, but iCloud re-applies the flag within minutes, so the fix is to put the
venv somewhere that is not synced:

```bash
python3 -m venv ~/.virtualenvs/eu-einvoice-bridge
~/.virtualenvs/eu-einvoice-bridge/bin/pip install -e ".[dev]"
```

Syncing a virtualenv is a bad idea regardless — thousands of files, none of them
worth keeping.

The test suite is unaffected either way: `pythonpath = ["src"]` in `pyproject.toml`
puts the source tree on `sys.path` directly, so tests never depend on the editable
install resolving.

## Documentation

| Document | Contents |
|---|---|
| [Design spec](docs/specs/2026-09-20-ksef-en16931-bridge-design.md) | Architecture, data model, the eight design decisions, error handling, test strategy |
| [Known limitations](docs/specs/2026-09-20-ksef-en16931-bridge-design.md#14-已知限制) | What this deliberately does not do |
| [Background](docs/BACKGROUND.md) | ViDA and national mandates, why Poland rather than Hungary, first-party sources |
| [Phase 1 plan](docs/plans/2026-09-20-phase1-implementation-plan.md) | Step-by-step plan and acceptance criteria |

The design documents are written in Traditional Chinese; the spec's structure and the
BT/BR identifiers throughout are language-independent.

## Third-party assets

`assets/` holds official schemas and validation artefacts, vendored unmodified so
that every test runs offline. Each keeps its own licence, separate from this
repository's code:

| Path | Source | Licence |
|---|---|---|
| `assets/en16931/` | [ConnectingEurope/eInvoicing-EN16931](https://github.com/ConnectingEurope/eInvoicing-EN16931), `validation-1.3.16` | EUPL v1.2 |
| `assets/ubl21/` | OASIS UBL 2.1 | OASIS copyright, notices retained |
| `assets/fa3/` | [CIRFMF/ksef-docs](https://github.com/CIRFMF/ksef-docs), pinned commit | MIT, Polish Ministry of Finance |

See [`assets/README.md`](assets/README.md) for provenance, pinned versions, and how
each copy was checked against the officially published one.

## Licence

Source code: MIT — see [LICENSE](LICENSE). Vendored assets: each under its own licence, as above.
