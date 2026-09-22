# EU E-Invoice Bridge

[![CI](https://github.com/edyiaeonian/eu-einvoice-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/edyiaeonian/eu-einvoice-bridge/actions/workflows/ci.yml)
[![KSeF TEST](https://github.com/edyiaeonian/eu-einvoice-bridge/actions/workflows/integration.yml/badge.svg)](https://github.com/edyiaeonian/eu-einvoice-bridge/actions/workflows/integration.yml)

Turning one neutral invoice into two things at once: an **EN16931** invoice in UBL 2.1
syntax, and the **FA(3)** XML that Poland's KSeF requires.

- **One neutral model, two outputs** — EN16931 UBL and Poland's FA(3), each validated
  offline against the official schemas and Schematron rules
- **Every mismatch between the two standards gets a deliberate answer** — including
  refusing to emit a file that is certain to be rejected
- **Checks no validator makes** — a tax code that contradicts the buyer is valid XML
  that the schema and KSeF both accept; this refuses it
- **Submits to Poland's real KSeF TEST environment daily in CI, with no secrets** — a
  self-signed identity is created per run
- **An unanswered send is never retried** — interrupted submissions resume from a
  local record

All three phases are complete: [validation pipeline](docs/plans/2026-09-20-phase1-implementation-plan.md),
[FA(3) and mismatch handling](docs/plans/2026-09-21-phase2-implementation-plan.md),
[KSeF submission](docs/plans/2026-09-21-phase3-implementation-record.md).

## Why this exists

The EU set a semantic standard for e-invoices, EN16931. It says what an invoice must
*mean* — which parties, which tax categories, which totals. It does not say what file
a tax authority will accept.

For standard VAT invoices, Poland's KSeF accepts one syntax: its own national schema,
FA(3). And FA(3) is **not** a one-to-one mapping of EN16931. Some EN16931 business
terms have no Polish equivalent. Poland requires fields the European standard never
defined. An invoice in a foreign currency still has to report its tax in PLN, and the two
standards record that in shapes that share nothing.

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

Each case gets a deliberate answer, and they are not the same answer:

| Case | Handling |
|---|---|
| Both standards have the field | Map directly, converting its shape where needed |
| Poland-only, absent from EN16931 | Lives in a named `PolishExtras` model, kept out of the core |
| EN16931 has it, FA(3) does not | Dropped on the Polish path — but **reported, never silently** |
| **FA(3) requires it, the neutral model has no field** | **Fail before emitting.** A file certain to be rejected is not worth sending |

The third and fourth cases pull in opposite directions, which is the point. Dropping a
field the other side cannot hold is survivable if you say so. Emitting a file you
already know is invalid just spends a submission to learn nothing.

**Building the Polish path showed the third case needed a limit.** A prepayment and a
document-level allowance both subtract money, and they land on opposite sides:

| | Changes the tax base? | In FA(3) |
|---|---|---|
| Prepayment (BT-113) | No | `Rozliczenie/Odliczenia`, which adjusts only the payable amount — a clean fit |
| Document-level allowance (BG-20) | **Yes** (BR-S-08) | Nothing. Dropping it would make the Polish invoice state a different tax from the UBL one |

So a field may only be dropped if losing it changes no amount. One that changes the
tax base and cannot be carried faithfully blocks FA(3) output instead.

**The clearest single example is VAT itself.** EN16931 describes tax as a category plus
any rate. FA(3) uses a closed set of Polish codes — `23`, `8`, `0 WDT`, `zw`, `oo` —
each with its own subtotal slot. A 19% standard rate is perfectly valid EN16931 and has
nowhere to go in FA(3).

**A worked example — foreign currency.** One requirement, two unrelated shapes.
EN16931 records only the converted VAT total (BT-111) and never the exchange rate;
FA(3) records the rate on every line (`KursWaluty`) and the converted tax per rate
slot (`P_14_xW`). The rate therefore lives in the neutral model and both are derived
from it. The rounding had to be decided once: at 4.2567 PLN/EUR, converting each rate
group gives 97.90 + 13.62 = **111.52**, while converting the 26.20 total gives
**111.53**. Either could be defended; the two documents disagreeing could not. Tax is
converted once per group and both outputs sum those same figures.

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

Asking for FA(3) adds a mapping layer that runs before any XML exists. The same
`invoice.json` cannot be expressed in FA(3), and the report says exactly why — errors
block the output, warnings name what would be left behind:

```
$ einvoice convert invoice.json --format fa3
invoice.json: 3 problems, 2 warnings

FA(3) mapping (3 errors, 2 warnings)
  FA3-NO-DECLARATIONS
    at extras
    FA(3) requires the Polish statutory declarations -- JST, GV and the Adnotacje ...
  FA3-DOC-ALLOWANCE  BG-20, BG-21
    at allowance_charges
    document-level allowances and charges change the tax base (BR-S-08) and FA(3) ...
  FA3-OO-BUYER  BT-48, BT-151
    at buyer.vat_id
    FA(3) reverse charge (oo, P_13_10) is the domestic procedure, with a Polish ...
  FA3-DROP-BT30  (warning)  BT-30
    at seller.legal_registration_id
    ...
  FA3-DROP-REASON  (warning)  BT-120
    at lines.1.exemption_reason
    ...
no output written
```

The third error is one [no validator makes](#checks-no-validator-makes): the
consulting line is a reverse-charge service to a German company — correct in
EN16931, but FA(3)'s `oo` is the *domestic* procedure.

Exit codes separate the two questions a caller has: `0` valid, `1` the invoice is
wrong, `2` the file could not be read at all.

Severity follows the flag on each official rule. Of the 979 assertions, 281 are fatal
and 698 are warnings; only fatal ones make an invoice invalid. Warnings are listed,
marked `(warning)`, and neither change the exit code nor stop `convert` from writing
the file.

### Sending it to KSeF

`submit` runs the same FA(3) checks, then encrypts the invoice and sends it to
Poland's **TEST** environment, waits for the verdict and saves the official receipt
(UPO):

```
$ einvoice submit invoice.json --test-seller
created a self-signed KSeF TEST identity for NIP 5046948298 in certs/
invoice.json: accepted by KSeF TEST
  KSeF number: 5046948298-20260921-906675C00000-7C
  UPO:         state/CLI_a555c6.upo.xml
```

Three decisions carry most of the weight:

**Authentication is an XAdES signature with a self-signed certificate.** The plan was
a KSeF token, until the API showed that tokens can only be created *after*
authenticating. KSeF's TEST environment (only TEST) accepts a self-signed certificate
shaped like a company seal (`organizationIdentifier` = `VATPL-<NIP>`); the program
makes one on first use, so a fresh clone can submit with no manual setup.

**An unanswered send is never retried.** Asking for a status twice is harmless;
sending an invoice twice is not. The client has two paths: idempotent calls are
retried with exponential backoff, and the send, the session opening and the one-time
token redemption are not. Making the send retryable, as a check, turns four tests red.

**The record is written before the send.** A send that got no answer leaves no
reference number and no way to know whether KSeF has the invoice. So the state file
says `sending` first; a rerun looks for the SHA-256 of the exact bytes sent among the
session's invoices, and resumes if found or stops and says so if not. The XML sent is
kept, because FA(3) carries a generation timestamp and re-rendering changes the hash.

| Exit code | Meaning |
|---|---|
| `0` | Accepted; UPO saved |
| `1` | The invoice is wrong — locally, or KSeF rejected it (with its reason) |
| `2` | The file could not be read |
| `3` | The invoice is in KSeF and only the follow-up is unfinished — still processing, or accepted with the UPO not yet downloaded. Run the same command again |
| `4` | The submission failed: authentication, a refused request, or a previous send that cannot be accounted for |

KSeF accepts invoices only from the NIP that authenticated. Without `--test-seller`,
a different seller is refused before anything is sent.

A number is taken only while KSeF may hold the invoice. If KSeF rejects it, or
turns the send request down (a 4xx: it never looked at the invoice), a corrected
version can go out under the same number. Changing an invoice that is still in
flight or already accepted is refused as a conflict.

### Checks no validator makes

The FA(3) schema accepts any rate code with any buyer, and KSeF does not tie the
two together either. So an intra-EU supply (`0 WDT`) invoiced to a Polish company
is valid XML — and wrong. The mapping layer checks what the law makes checkable:

| Code | Rule | Severity |
|---|---|---|
| `0 WDT` | Buyer needs a VAT number from another member state | error |
| `oo` | Domestic reverse charge: buyer needs a Polish NIP. Cross-border it is `np I` or `np II`, depending on whether the supply is a service under art. 100(1)(4) — which the model does not record, so it refuses rather than guess | error |
| `0 EX` | Buyer in Poland: legal (export turns on the goods leaving the EU, not on the buyer), but unusual | warning |

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
never the first validator. Modules are split where failures differ: bad data, a bad
key and a sandbox that is down each surface in their own module, and everything up to
`ksef` — encryption included, checked against NIST vectors — is testable offline.

## Quick start

Requires Python 3.12+. Install with either:

```bash
# A: pip
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"

# B: uv, with the exact versions CI tests against (uv.lock)
uv sync --locked --extra dev
```

Both create `.venv/`. Then:

```bash
./.venv/bin/einvoice validate examples/invoice.json
./.venv/bin/einvoice convert examples/invoice.json -o invoice.xml

# Poland's FA(3); the same input also converts to UBL
./.venv/bin/einvoice validate examples/invoice-fa3.json --format fa3
./.venv/bin/einvoice convert examples/invoice-fa3.json --format fa3 -o invoice-fa3.xml

# Invoiced in EUR, VAT reported in PLN
./.venv/bin/einvoice convert examples/invoice-eur.json --format fa3

# Send to Poland's TEST environment -- no account or credentials needed
./.venv/bin/einvoice submit examples/invoice-fa3.json --test-seller
```

`examples/invoice.json` is refused for FA(3), on purpose, for the three reasons shown
[above](#what-it-looks-like). `submit` creates a self-signed test identity in `certs/`
on first use and writes records and UPOs to `state/`; both are git-ignored.

### Tests

```bash
./.venv/bin/pytest                   # offline, Schematron included
./.venv/bin/pytest -m integration    # the real KSeF TEST environment
./.venv/bin/ruff check src tests && ./.venv/bin/mypy    # lint, strict types
```

The offline suite runs against vendored official artefacts and a mocked KSeF. The
integration test runs [every morning on GitHub Actions](.github/workflows/integration.yml)
as a separate workflow, so a sandbox outage never turns the main CI badge red. GitHub
pauses scheduled workflows after 60 days without repository activity; if the KSeF TEST
badge stops updating, re-enable it from the Actions tab. CI installs from `uv.lock`, so
a new upstream release cannot change what is tested without a commit.

Something not working? See [Troubleshooting](docs/TROUBLESHOOTING.md) — including a
silent macOS failure when the venv sits in an iCloud-synced folder.

## How this was built

The code was written with an AI coding assistant (Claude Code). My part was the
direction and the judgement calls: choosing the problem and the scope, reviewing each
design spec and its acceptance criteria before implementation began, and putting the
finished work through independent reviews. Review findings were verified against the
official rules and the running code before anything changed — most were adopted, and
where one was not, the reason is recorded (the export and cross-border reverse-charge
checks are examples). The [phase 3 record](docs/plans/2026-09-21-phase3-implementation-record.md)
notes where the implementation departed from the spec, and why.

## Documentation

| Document | Contents |
|---|---|
| [Design spec](docs/specs/2026-09-20-ksef-en16931-bridge-design.md) | Architecture, data model, the eight design decisions, error handling, test strategy |
| [Known limitations](docs/specs/2026-09-20-ksef-en16931-bridge-design.md#14-已知限制) | What this deliberately does not do |
| [Background](docs/BACKGROUND.md) | ViDA and national mandates, why Poland rather than Hungary, first-party sources |
| [Phase 1 plan](docs/plans/2026-09-20-phase1-implementation-plan.md) | Step-by-step plan and acceptance criteria |
| [Phase 2 plan](docs/plans/2026-09-21-phase2-implementation-plan.md) | FA(3) serializer, the four mismatch categories, multi-currency |
| [Phase 3 record](docs/plans/2026-09-21-phase3-implementation-record.md) | KSeF submission: decisions, evidence, live run |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | macOS/iCloud venv failure, sandbox maintenance window |

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
| `assets/fa3/` | [CIRFMF/ksef-api](https://github.com/CIRFMF/ksef-api) (formerly `ksef-docs`), pinned commit | MIT, Polish Ministry of Finance |

See [`assets/README.md`](assets/README.md) for provenance, pinned versions, and how
each copy was checked against the officially published one.

## Licence

Source code: MIT — see [LICENSE](LICENSE). Vendored assets: each under its own licence, as above.
