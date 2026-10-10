# CutoverGuard

**Same total. Different owner.** A cloud-migration review prototype built for the
BNY Financial Services Challenge and the Databricks challenge. It compares source
and candidate account/transaction snapshots, exposes ownership errors hidden by
matching totals, and exports exact evidence for human review.

## Run locally

```sh
python3.12 -m venv .venv
.venv/bin/python cutover_app.py
```

Open http://127.0.0.1:8502. Python 3.12 and the standard library are enough for
local use; no cloud account or API key is required. Choose defective/fixed examples,
inspect Findings and Account impact, then export JSON/CSV. Use `--port 8503` if
needed. [Offline preview](cutover-preview.html) supports saved examples; arbitrary
uploads and imported-report validation require the running app.

## The financial demonstration

| Account | Source | Candidate | Candidate minus source |
|---|---:|---:|---:|
| 0001 | $1,000 | $2,000 | +$1,000 |
| 0002 | $2,000 | $1,000 | −$1,000 |
| Firm total | $3,000 | $3,000 | $0 |

Two synthetic event-account mismatches block the defective candidate even though
firm totals match. Correcting ownership yields zero differences and ready for
human review. The $2,000 sum of absolute account differences and $3,000 changed
transaction notional are **not financial losses**.

Money uses integer minor-unit strings; identifiers preserve leading zeros;
currencies stay separate. Duplicate keys block ambiguous joins. Malformed rows
remain visible in quarantine, and incomplete evidence never becomes approval.
Completion watermarks and dependency edges are supplied declarations. Supported
currencies are USD/EUR/GBP, scale 2. The local reference is bounded to 5,000 rows
per input array and a 4 MB request, not a million-row production engine.

## Databricks: executed SQL and native review

Actual synthetic defective and fixed SQL runs persist Delta findings, impacts and
totals. Every remote result is compared with the exact local oracle before it is
labeled verified. The native dashboard and linked Genie space were created and
live SQL/configuration verified. Visible dashboard values and one revised Genie
answer were inspected; full visual/answer evaluation is not claimed.

```sh
.venv/bin/python scripts/run_cutoverguard_databricks.py --warehouse-id YOUR_WAREHOUSE --fixture defective --output cutover_results/new-defective.json
.venv/bin/python scripts/run_cutoverguard_databricks.py --warehouse-id YOUR_WAREHOUSE --fixture fixed --output cutover_results/new-fixed.json
```

These create isolated tables and may start compute; no existing tables are
replaced or dropped. Local import of saved reports recomputes financial claims,
but cannot authenticate their provenance. It is not a continuous live feed.

A file-based public-retail path exports CSV snapshots to an existing Unity Catalog
Volume, reads them into Delta and runs the reconciliation SQL. This avoids placing
transaction input in SQL text. Actual execution verified **1,467 historical invoice
lines per side**, all imported rows and the full SQL findings/impacts/totals against
the local oracle. The controlled defective candidate has two ownership breaks;
GBP totals match while account differences do not. See
[file ingestion](docs/cutoverguard-file-ingestion.md) for evidence and commands.
This full-engine demonstration is separate from the large SQL experiment below.

## Measured proof of ownership errors

Across **700 designed synthetic trials** (100 per condition), CutoverGuard detected
all 600 injected defect cases with zero critical false alarms on 100 clean controls.
An illustrative count/currency/day-total/owner-checksum baseline missed ownership
swaps, compensating pennies, equal-value event replacements and same-day timestamp
shifts; it caught added duplicates and single amount changes.
[Exact results and limits](experiments/randomized-002.md) are included. These are
controlled sensitivity results, not production accuracy estimates.

A separate Databricks SQL experiment compared **7,500,000 synthetic TPC-H orders
per side** and found exactly **50 deliberately swapped owner assignments**.
Counts, totals and aggregate checksums still matched. Independent Python checks
verified the injected findings and customer-bucket effects. This is a narrow SQL
experiment, not full-engine equivalence, banking data, loss prevention or GPU timing.
[Scale evidence](evidence/tpch-scale-summary.json) and
[reproduction instructions](docs/tpch-scale.md) document the scope.

Live SQL and a user screenshot verified the historical-retail proof panel:
four aggregate checks PASS while two wrong-owner rows are BLOCKED.
[Saved values](evidence/aggregate-checks-summary.json) omit private workspace IDs.
Existing row-diff and owner-aware hash tools can catch swaps too; CutoverGuard adds
financial allocation effects and evidence for human review.

The final dashboard generator puts the TPC-H run first, followed by real public
records and a single evidence page. It adds a fifth aggregate control (order-key
checksum); the 700-trial study retains its documented four-control baseline.
The crosswalk migration generator prepares a Lakeflow promotion gate, with
separate defective/corrected SQL. Empty serverless pipeline creation is confirmed;
live gate failure and corrected promotion remain unverified.
See [dashboard and gate deployment](docs/tpch-dashboard-and-gate.md).

The dollar-valued native counter update has passed live SQL/configuration verification;
refreshed rendering remains pending. Applying it in another workspace requires its
existing saved state. It uses a backup, exact SQL checks and ETag to
protect unrelated draft changes; it does not publish the dashboard:

```sh
.venv/bin/python scripts/update_cutoverguard_dashboard.py --apply --output native_databricks/new-run
```

[Native setup/recovery](docs/cutoverguard-native.md) covers resource creation and
inspection. Genie explains verified SQL tables; generated SQL and prose need human
checking. Keep a pre-verified answer/chart as presentation backup.

## Historical public data and controlled replay

UCI Online Retail validation ran locally: 2,000 invoice lines scanned, 1,467 retained,
533 excluded for missing CustomerID, six controlled cases verified. Historical GBP
invoice amounts and customer-derived buckets are not bank cash or bank accounts.
Candidate defects were deliberately injected, not observed in a real migration.
[Source, license and exclusions](docs/cutoverguard-real-data.md) are documented.
Optional XLSX ingestion needs `openpyxl`.

The app also includes an exact-integer replay of the two synthetic events through
defective/fixed mappings. It is a local controlled parallel-run illustration,
not a production digital twin. Replay fingerprints are separate from main exports.

## AMD: separate measured compute path

AMD ROCm runs on a separate Radeon machine over SSH, **not inside Databricks**.
The saved 1M-row synthetic allocation benchmark measured CPU tensor aggregation
2.215 ms vs ROCm 1.400 ms median (1.58× ratio, approximately 0.815 ms difference),
with exact oracle agreement. These are component timings, not full reconciliation.

The updated benchmark additionally measures a labeled naive Python loop and a
separately bounded resident-GPU calculation. A new 10M-row run is prepared but
has not been measured. See [AMD instructions](amd/README.md); do not extrapolate
production speedups or compare different timing boundaries as equivalents.

## Checks and limitations

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
node --check cutover_static/app.js
```

This is a hackathon prototype: no BNY API integration, client records, production
connectors, automatic remediation, regulatory certification or verified savings.
Ready for review means supported checks passed on supplied snapshots; a human
still decides whether to proceed. 

[Contract](docs/cutoverguard-contract.md), [platform workflow](docs/cutoverguard-databricks.md)
and [demo draft](docs/cutoverguard-demo.md) explain the implementation and evidence.
The initial GitHub publication is committed; demo video and Devpost submission
remain pending.

## Reproducing this publication snapshot

The included cloud reports and native payloads capture the original synthetic demo; their table names do not imply access in another workspace. Run fresh SQL fixtures in your own workspace, then pass their report paths to native setup. Native update/recovery commands require your own saved state. Public-retail extracts are regenerated using the documented UCI download and attribution; raw or customer-derived extracts are excluded here. The included ingestion tests use invented invoice lines.
