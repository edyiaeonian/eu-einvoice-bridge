#!/usr/bin/env bash
# Runs the installed einvoice command against the examples. Used by CI twice:
# once from the development install, once from a wheel installed the way a
# user would, where the package is not the checkout.
set -euo pipefail

out=$(mktemp -d)
einvoice validate examples/invoice.json
einvoice convert examples/invoice.json -o "$out/invoice.xml"
grep -q "urn:cen.eu:en16931:2017" "$out/invoice.xml"
einvoice validate examples/invoice-fa3.json --format fa3
einvoice convert examples/invoice-fa3.json --format fa3 -o "$out/invoice-fa3.xml"
grep -q "http://crd.gov.pl/wzor/2025/06/25/13775/" "$out/invoice-fa3.xml"
einvoice validate examples/invoice-eur.json
einvoice validate examples/invoice-eur.json --format fa3
# submit needs the KSeF sandbox, so this only checks that it loads.
einvoice submit --help > /dev/null
