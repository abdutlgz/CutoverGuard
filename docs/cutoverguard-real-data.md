# Public-data validation — UCI Online Retail

The user downloaded and ran the actual UCI workbook in their Terminal. In the first
2000 scanned records, 1467 were retained and 533 were excluded for missing CustomerID.
All six controlled cases passed. The agent then inspected the saved file and reports,
verified the source-file SHA256, independently recalculated retained account totals
from raw Quantity and UnitPrice, revalidated all six reports, and reproduced the
cases from the downloaded workbook. These results cover this extract and controlled
candidate changes only. Databricks execution on this dataset remains unverified;
the existing cloud runner intentionally accepts synthetic inputs only.

## Run on the user's terminal

Optional XLSX reading dependency, installed only if needed:

```sh
.venv/bin/python -m pip install openpyxl
.venv/bin/python -m pip check
```

Then one command downloads the official archive, reads its workbook without editing
it, selects at most 2000 original logical rows, and validates six local cases:

```sh
.venv/bin/python scripts/validate_public_retail.py --download --limit 2000 --output public_retail_validation
```

`--download` checks for openpyxl before fetching. A download/authentication/network
failure produces a useful error rather than any simulated success. To use an already
downloaded file:

```sh
.venv/bin/python scripts/validate_public_retail.py --xlsx '/path/Online Retail.xlsx' --limit 2000
```

CSV requires no additional dependency:

```sh
.venv/bin/python scripts/validate_public_retail.py --csv /path/retail.csv --limit 2000
```

Required CSV headers: InvoiceNo, Quantity, InvoiceDate, UnitPrice, CustomerID.
UTF-8/BOM is supported. ISO dates are preferred. Other CSV dates use the explicitly
recorded default `%m/%d/%Y %H:%M`; use `--date-format '%d/%m/%Y %H:%M'` for a day-first
export. Locale is not guessed. Output defaults to `public_retail_validation`.

## What the source represents

[UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail) is a public
retail invoice-line dataset, licensed CC BY 4.0. Retain that source/license attribution
when using or redistributing its data. [Official archive](https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip).

The adapter measures **Quantity × UnitPrice invoice-line value**, denominated in GBP.
This is not observed cash, a bank balance, settlement, a verified receivable booking,
or an actual legacy-to-cloud migration. Customer-derived `receivable:<CustomerID>`
buckets are an explicit demonstration mapping, not real bank accounts. Invoice IDs
may repeat across lines, so event IDs use original logical record numbers, such as
`uci-row-000000002` for the first data record after the header. CSV CustomerID strings,
including leading zeros, are preserved; existing numeric workbook cells have already
lost any original string formatting and are serialized as supplied numeric values.

Positive and negative quantities, including returns, are retained. Decimal arithmetic
computes exact pennies without floats, rounding, or silent numeric underflow. Fractional
penny results are excluded with a reason; a fractional-penny price may be accepted only
when the full quantity-times-price result is an exact penny amount. Nonfinite values,
fractional quantities, negative unit prices and excessively large values are excluded.

Naive InvoiceDate values are assumed Europe/London. Ambiguous/nonexistent DST times
are excluded rather than guessed. Explicit offsets are preserved as instants, converted
to UTC. More than six fractional timestamp digits are rejected. XLSX cells are read
using openpyxl `read_only=True, data_only=True`; no workbook is authored or rewritten.
[Read-only reader documentation](https://openpyxl.readthedocs.io/en/3.1.2/api/openpyxl.reader.excel.html).

## Selection and completeness

The first N original records are scanned, with N between 1 and 4999; one slot is
reserved for an injected duplicate. Defaults to 2000. Invalid/missing-customer rows
are excluded in the extract manifest with their original record and reason. They are
not silently counted as valid reconciled data. Unscanned rows are outside scope and
are not counted. No random sampling or customer guessing occurs.

The source snapshot covers only selected valid records. Its window is first selected
UTC event through one microsecond after the last selected event. The completion
watermark is a declaration about that chosen extract, not proof of full retailer data
completeness. Capture metadata uses the local source file modification timestamp and
states that assumption. Snapshot identity hashes selected content, adapter metadata
and source-file hash; changing selection changes the snapshot identity.

The engine supports explicitly declared scale-2 GBP as well as USD/EUR; currencies
are never relabeled or combined. Existing USD/EUR fixtures, rule version and comparison
fingerprints are preserved.

## Controlled validation and success

The historical source stays unchanged. Each candidate is an independently copied,
deliberately altered test case:

1. Identity baseline: no primary findings.
2. Ownership swaps: exactly two event-account findings across distinct customers.
3. Missing/extra: exactly the removed and injected event IDs.
4. Compensating one-penny changes: exactly two amount findings despite zero net change.
5. Duplicate keys: exactly one event-key and one profile-key finding; totals unavailable.
6. Customer mapping: exactly one customer ownership mapping finding.

Expected primary tuples are recorded separately from detector output. Derived account
balance findings do not inflate those counts. Success requires exact tuple equality,
exact counts and expected review states. It does not measure real migration accuracy,
observed failures, financial savings, or financial-industry representativeness.

Outputs include six `*-bundle.json` and six `*-report.json` files,
`extract-manifest.json` (raw observations and exclusions), and
`validation-summary.json` (expected versus observed primary findings). Import a report
using the dashboard's **Import saved report** control. Labels say historical source
and controlled candidate; execution remains local. Preserve the manifest alongside any
reported validation results.
