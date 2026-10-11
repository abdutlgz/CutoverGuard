# Separate TPC-H SQL ownership experiment

**Live run verified October 10, 2026: 7,500,000 source and candidate orders,
50 injected wrong-owner rows detected exactly.** Saved evidence is
`tpch_results/run-001/report.json`; its findings and customer effects were also
rechecked locally against the independent injection specification.
This script is separate from the CutoverGuard engine and its existing cloud demonstration.
It is a synthetic wholesale benchmark with deliberately injected ownership swaps,
not observed banking data, real customer losses, or an actual migration.

## Read-only discovery

```sh
.venv/bin/python scripts/run_tpch_scale.py \
  --warehouse-id 515dff82a7a2c282 --discover \
  --output tpch_results/discovery-001
```

This DESCRIBEs `samples.tpch.orders` and computes its actual row count, unique keys,
null counts, total price units and checksums. It requires integer order/customer
keys, DATE order dates, and DECIMAL(scale 2, precision <=28) prices. FLOAT/DOUBLE
prices are rejected. No source access, schema shape or 1.5-million-row size is
assumed. Read-only SQL may start compute. Discovery output is locally reserved;
choose a fresh directory for another discovery.

## Execute the full-table experiment

```sh
.venv/bin/python scripts/run_tpch_scale.py \
  --warehouse-id 515dff82a7a2c282 --execute \
  --output tpch_results/run-001
```

Optional `--profile NAME`, `--catalog NAME`, and `--schema NAME` select existing
access and the target schema. The identity requires SELECT on the sample table
and CREATE TABLE in the target schema. No privileges are granted automatically.

The experiment creates six fresh Delta tables: source snapshot, injection map,
candidate, issues, customer-bucket effects and metrics. It never changes the sample
source, replaces existing tables, or drops anything. Full source rows come from
CTAS, not local upload or SQL VALUES. Source keys/nulls/count/totals/checksums are
validated after freezing the snapshot.

Fifty orders are selected deterministically: the lexically first string order ID
per distinct customer, then the first 50 string order IDs. Python independently
pairs adjacent selected orders into **25 pairs**, swapping customer ownership on
exactly **50 distinct orders**. Prices and dates stay unchanged. Fewer than 50
eligible distinct customers fail explicitly. Zero customer balance effect is
possible for a pair with equal order prices; it still produces two ownership
findings.

Full outer joins examine all source/candidate orders for missing/extra keys,
ownership, amount or date changes. Every resulting finding must exactly equal the
independent Python injection specification. Full customer aggregations produce
account effects; nonzero effects must equal the independent 50-order injection
ledger. Source and candidate full-table counts, totals and checksums must match.
All numerical comparison uses integer/DECIMAL arithmetic.

Amounts are **arbitrary TPC-H price units multiplied by 100**. They are never
labeled USD or another observed currency. Negative customer-bucket effects are
allocation differences, not losses.

## What the checksum proves

For each raw customer ID string, hash UTF-8 with SHA-256, interpret its first 16
hex digits as an unsigned 64-bit integer, sum across orders, and reduce modulo
2^64. The SQL and Python definition match. Swapping owners preserves this
permutation-invariant checksum even when the exact order ownership is wrong.
The row-by-row keyed findings detect that error. The checksum is an illustrative
aggregate diagnostic, **not a row-aware integrity proof** or financial approval. Order-ID checksums are also aggregate diagnostics.

## Evidence and safe resumption

`state.json` records unique object names, discovery values, the exact injection
specification, completed stages, statement IDs and elapsed times. On success,
`report.json` records `verified_narrow_sql_experiment`, full observed metrics,
findings, account effects and limitations. It is not a CutoverGuard engine report;
do not import it through the engine-report UI or describe it as full local-oracle
equivalence. The independent oracle covers injected ownership findings and
incremental customer effects; the full unmodified ledger is aggregated in SQL.

```sh
.venv/bin/python scripts/run_tpch_scale.py \
  --warehouse-id 515dff82a7a2c282 --execute --resume \
  --output tpch_results/run-001
```

Resume skips completed creations and rechecks the frozen source and outputs. A
failed write with an ambiguous outcome leaves its attempted-stage marker and
blocks automatic retry. Inspect query history and use a fresh output directory
for a new attempt. No existing statement is blindly repeated. An earlier exported
report remains a snapshot; a failed subsequent validation marks state unverified.

Timings include CLI SQL submission, warehouse startup if needed, polling and full
result fetching. Discovery has its own elapsed interval; each subsequent query
has an interval. They are neither query-engine-only latencies nor GPU timings.
There is no CPU/GPU comparison or speedup claim. The saved run verifies the
7.5-million-order scale for this narrow SQL experiment, not the full engine.

Official sample-table reference: [Databricks SQL query examples](https://docs.databricks.com/aws/en/sql/user/sql-editor/write-queries),
checked October 10, 2026. This documents the table and field names; it does not
verify this workspace's access, schema or row count.
