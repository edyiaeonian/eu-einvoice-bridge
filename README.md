# EU E-Invoice Bridge

Turning one neutral invoice into two things at once: an **EN16931** invoice in UBL 2.1
syntax, and the **FA(3)** XML that Poland's KSeF is the only format it will accept.

> **Status: work in progress.** Phase 1 (offline validation pipeline) is under
> construction. See [the plan](docs/superpowers/plans/2026-09-20-phase1-implementation-plan.md).

---

## Why this exists

The EU set a semantic standard for e-invoices, EN16931. It says what an invoice must
*mean* — which parties, which tax categories, which totals. It does not say what file
a tax authority will accept.

Poland's KSeF accepts exactly one syntax: its own national schema, FA(3). And FA(3)
is **not** a one-to-one mapping of EN16931. Some EN16931 business terms have no
Polish equivalent. Poland requires fields the European standard never defined. An
invoice in a foreign currency still has to report its tax in PLN, so FA(3) carries an
exchange rate that EN16931 expresses a different way entirely.

Every country running a CTC (continuous transaction control) regime has this problem,
and there are a lot of them now — Italy since 2019, Hungary since 2018, Poland from
2026, France and Germany next. The EU's ViDA reform makes EN16931 mandatory for
cross-border trade in 2030 without making the national formats go away.

**So the interesting engineering question is not "can you call the API". It is: where
does the gap between the standard and the national format live in your code?**

This project's answer: **not in a conversion function.** Converting EN16931 directly
to FA(3) collapses into a pile of special cases, and the gap disappears into it. Here
both formats are produced from one neutral model by two independent serializers, so
each difference stays visible in the one place that owns it — and the four kinds of
mismatch get four deliberate answers, including one that refuses to emit a file at all.

## Architecture

```
                  ①  model              neutral, knows no country
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
      ② ubl                   ④ fa3      serialize
          │                       │
          └───────────┬───────────┘
                      ▼
                ③ validate                XSD + Schematron, both paths
                      │
                      ▼
                ⑤ crypto                  AES-256-CBC + RSA-OAEP
                      │
                      ▼
                ⑥ ksef                    submit, poll, fetch UPO

                  ⑦  cli
```

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

Tests run fully offline. The ones that need Poland's sandbox are marked `integration`
and skipped by default, so a sandbox outage never turns the suite red:

```bash
pytest -m integration
```

## Documentation

| Document | Contents |
|---|---|
| [Design spec](docs/superpowers/specs/2026-09-20-ksef-en16931-bridge-design.md) | Architecture, data model, the seven design decisions, error handling, test strategy |
| [Background](docs/BACKGROUND.md) | ViDA and national mandates, why Poland rather than Hungary, first-party sources |
| [Phase 1 plan](docs/superpowers/plans/2026-09-20-phase1-implementation-plan.md) | Step-by-step implementation plan and acceptance criteria |

## Third-party assets

`assets/` holds the official EN16931 validation artefacts, vendored unmodified from
[ConnectingEurope/eInvoicing-EN16931](https://github.com/ConnectingEurope/eInvoicing-EN16931)
at release `validation-1.3.16`. They are licensed under the **EUPL v1.2**, separately
from this repository's own code.

See [`assets/README.md`](assets/README.md) for provenance and
[`assets/en16931/LICENSE.txt`](assets/en16931/LICENSE.txt) for the licence text.

## Licence

Source code: MIT — see [LICENSE](LICENSE). Vendored assets: EUPL v1.2, as above.
