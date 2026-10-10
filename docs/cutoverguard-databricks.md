# CutoverGuard Databricks execution

The user's terminal verified authentication, SELECT 1 and new Delta table
creation/insertion/read-back. **The project reconciliation SQL has not yet run on
Databricks from this restricted agent environment.** Tests exercise CLI boundaries
and adapted relational SQL locally; they do not certify cloud dialect execution.
G3 remains pending until the commands below succeed, including the corrected rerun.

## Run from your normal terminal

No new token or external API is needed; this uses your existing Databricks CLI login.
From the KnightHacks project directory:

```sh
.venv/bin/python scripts/run_cutoverguard_databricks.py --warehouse-id YOUR_WAREHOUSE_ID --fixture defective --output cutover_results/defective-cloud.json
.venv/bin/python scripts/run_cutoverguard_databricks.py --warehouse-id YOUR_WAREHOUSE_ID --fixture fixed --output cutover_results/fixed-cloud.json
```

May start serverless SQL compute. Defaults to `workspace.default`; set `--catalog`,
`--schema`, `--profile` if needed. Supply `--input cutover_examples/defective.json`
to use an explicit bundle. Both input modes require synthetic data. Existing output
files are protected; choose new `--output` names for subsequent runs. These commands
create six uniquely named Delta tables per run and retain them. They never replace,
drop or overwrite existing tables. There is no automatic cleanup command.

On success import the report JSON through the dashboard's saved Databricks report
control. It is an **exported execution snapshot**, not a continuously live connection.
The neighboring `.execution.json` log contains table names, statement IDs, timestamps,
SQL result rows and oracle comparison flags. Failure also saves the execution log
when SQL execution has begun; status remains unverified and no report is issued.
Use Databricks Query History to inspect failed statements. Raw CLI errors are hidden
because service responses may contain sensitive details.

## Actual platform contribution

- `manifest`: canonical original input bundle and deterministic comparison run ID.
- `profiles`: source/candidate identifiers preserved as strings.
- `transactions`: source/candidate amounts stored as DECIMAL(38,0), UTC-normalized
  event timestamps and supplied windows. Original values remain in the manifest.
- `issues`: SQL FULL OUTER JOIN detects missing/extra profiles/events, changed
  customer IDs, event accounts, amounts, currencies and timestamps. Duplicate keys
  and unsupported inputs are rejected before any comparison join.
- `impacts`: SQL aggregates account/currency balances and candidate-minus-source
  deltas. Window filtering happens in SQL using normalized instants.
- `totals`: SQL calculates currency totals, net discrepancy, absolute account
  discrepancy, and affected transaction notional. No USD/EUR combination occurs.

The local Python engine validates inputs and independently calculates an oracle.
Actual platform finding tuples **and source/candidate values**, all account amounts
and all currency aggregates must match before cloud mode is emitted. Monetary SQL
results are fetched as integer strings. The shared engine packages detailed messages,
status and dependency graph only after SQL results agree. This is not an upload of
locally computed findings: issue/impact/totals tables are computed by SQL from the
persisted input tables. Derived business findings are represented by the impact rows;
primary findings persist separately in the issues table.

## Honest scope and failure behavior

Remote runner supports valid, unique-key, complete synthetic bundles, including
ownership changes, missing/extra events, amount/currency/timestamp differences and
profile differences whose links remain valid. Quarantined records, duplicate keys,
invalid account links and unknown metadata checks error explicitly; local review
remains available. Amount magnitudes are restricted below 10^30 to leave aggregate
headroom within DECIMAL(38,0) for the bounded input size. No floating-point money.

Polling has a per-statement timeout (default180 seconds; `--timeout`1–900).
Timeout, poll failure and keyboard interrupt request cancellation when an active
statement ID has been received. An interruption during initial submission may leave
its ID unknown; inspect query history in that case. Cancellation is a
request, not a guarantee that an already-completed CREATE TABLE was undone. Partial
Delta tables remain for inspection. INLINE paginated results are fetched in full;
truncation, missing rows and unexpected external links cannot produce a verified run.
The runner does not stop the warehouse or manage external accounts.

Databricks SDK/runtime is unnecessary. Python3.12 standard library + installed
Databricks CLI suffice. GPU/AMD/ARCC is not required for these joins and exact sums.
No client records, private employer data or credentials are uploaded.
