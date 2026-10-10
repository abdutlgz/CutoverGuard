# TPC-H dashboard and two-stage migration gate

## Dashboard

Run from the preparation workspace, where your verified scale and retail reports exist:

```sh
.venv/bin/python scripts/build_tpch_dashboard.py --verify-sql
```

Import `native_databricks/final/CutoverGuard.lvdash.json`. The current generator
checks unique dataset, page and per-page widget names before saving. Three pages:
7.5 million orders; real public retail records; evidence and limitations.
The ~60-second counter is the sum of recorded original query submission/poll/fetch
intervals, not engine-only timing or a speedup. TPC-H uses arbitrary price units,
not currency. The 700-trial study uses four aggregate controls; the scale dashboard
adds an order-key checksum as a fifth control.

The new scale queries were executed successfully and verified against exact
saved values, findings and customer effects. Visual rendering still needs inspection.
A fresh public checkout must run the scale and retail workflows first and supply
its own `--report`, `--retail`, and `--corrected` paths. Templates under
`native_databricks/ready` use placeholders and are not live workspace evidence.

## Step 1: persist migration evidence

The blank Python transformation in an empty pipeline is not an execution failure.
Configure and load SQL before running it.

```sh
.venv/bin/python scripts/build_migration_gate.py \
  --evidence-schema cutoverguard_migration_bad
```

This creates readable `defective.sql`, `corrected.sql`, and `promotion-gate.sql`
in `databricks_demo/migration-final`. Use `--report` for your own scale report.
The defective/corrected files now contain **evidence only**, with no failing gate
and no promotion target. Run only one active transformation per pipeline.

1. Use the empty serverless development pipeline. Set default catalog `workspace`
   and create a dedicated schema `cutoverguard_migration_bad`; do not use `default`.
2. Close settings. Choose SQL for the empty transformation (or create a SQL file),
   and paste the full contents of `defective.sql`. Save.
3. Click **Run pipeline**, not Run file. Wait for a completed update.
4. In SQL Editor inspect:

```sql
SELECT * FROM workspace.cutoverguard_migration_bad.gold_cutover_decision;
SELECT count(*) FROM workspace.cutoverguard_migration_bad.gold_order_issues;
SELECT * FROM workspace.cutoverguard_migration_bad.gold_customer_effects
WHERE delta_scaled_units <> 0;
```

A succeeded run with persisted findings and a blocked decision is the evidence
checkpoint. If compilation, permissions or compute fail, diagnose that error;
it is not proof the financial gate worked.

The swapped customer crosswalk remaps **every order** belonging to 50 selected
customers. Do not reuse the original experiment's 50 wrong-owner-order count:
this new count must be measured. Bronze retains legacy and crosswalk records;
silver uses DECIMAL amounts, DATE converted to an explicit UTC midnight instant using a Z-suffixed string and TIMESTAMP_LTZ, typed keys and
explicit quarantines. Gold checks row ownership, amounts, timestamps, crosswalk
relations, duplicate keys, quarantine, completeness and matching totals. The legacy
count, total and owner/order checksums are pinned to the verified extract manifest.
These aggregates do not cryptographically authenticate every legacy row.

## Step 2: test promotion separately

Use a second empty serverless development pipeline in another new schema, e.g.
`cutoverguard_promotion_bad`. Paste only `promotion-gate.sql`. It reads the persisted
evidence from the first schema; it does not rebuild or erase those gold tables.
Freeze the evidence schema while testing promotion. This prototype does not provide
an atomic production cutover across concurrently changing tables.

The gate has `EXPECT (status = 'ready') ON VIOLATION FAIL UPDATE`, and
`promoted_orders` explicitly depends on it. Run this pipeline after Step 1 succeeds.
The desired failure must name **no_wrong_owner_promotion**. Check that no bad
candidate rows were newly promoted. A previously successful target can retain its
old data after a failed update; stale rows are not a newly promoted bad candidate.

For correction, run `corrected.sql` in a fresh evidence schema
`cutoverguard_migration_fixed`. Verify zero findings/quarantine, unchanged full
counts/totals and a ready decision. Regenerate the gate with
`--evidence-schema cutoverguard_migration_fixed`, run it in a fresh promotion schema,
and confirm the full expected promoted row count. Save both update IDs and results.
Both isolated promotion tests now ran: defective rejected on `no_wrong_owner_promotion`; corrected passed and promoted 7,500,000 rows. See the verified results below.

Databricks documents that a failed expectation can stop its flow while parallel
flows proceed; separate orchestration provides stronger control. This two-stage
workflow makes persisted evidence a prerequisite rather than assuming it survives
a failing update. [Expectations documentation](https://docs.databricks.com/aws/en/ldp/expectations),
[SQL pipeline development](https://docs.databricks.com/aws/en/ldp/developer/sql-dev).

This is a controlled synthetic benchmark migration, not banking data, a leading-zero
collision, full local-engine equivalence, production approval or a BNY integration.

## Timestamp compatibility fix

The first live update failed with DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT for timestampNtz.
The generator now uses TIMESTAMP_LTZ with an explicit T00:00:00Z suffix on both sides,
preserving the UTC-midnight convention without requiring timestampNtz on pipeline tables.
Timestamp rendering can follow the session timezone; comparisons use the absolute instant.
[Databricks timestamp type](https://docs.databricks.com/aws/en/sql/language-manual/data-types/timestamp-type).

## Verified defective evidence run

Update `59b5383f-bb13-4c3f-9c7e-a84c6ea8cc43` completed in the authenticated UI.
The gold decision preview showed 7,500,000 source and candidate rows, 811 order findings,
50 crosswalk findings, zero quarantined rows, zero duplicate crosswalk keys and blocked status.
Both totals were 113343921524625 scaled price units. This is the crosswalk experiment,
not the earlier 50-order swap experiment. The timestamp retry first encountered a missing
internal materialization; full table refresh rebuilt the dedicated synthetic pipeline successfully.
The separate promotion tests below establish the gate behavior on these frozen demonstration snapshots.


## Verified independent count and promotion tests

An independent SQL filter on the frozen source, using the 50 literal authored customer IDs,
returned **811 orders across 50 customers**. It did not read the gold findings.
The reproducible query is `evidence/independent-crosswalk-count.sql`.

| Test | Observed result |
| --- | --- |
| Defective promotion | FAILED on `no_wrong_owner_promotion`; downstream output SKIPPED |
| Defective output catalog check | 0 `promoted_orders` tables in its fresh target schema |
| Corrected evidence | 0 ownership findings, 0 crosswalk findings, 0 quarantined records |
| Corrected promotion | COMPLETED, gate expectation met; 7,500,000 promoted rows |
| Corrected output versus silver source | 0 differences in keyed owners, amounts and UTC instants |
| Defective customer effects | 50 changed buckets, signed effects sum exactly to 0 |

Defective promotion update: `72307ca9-7504-4ad9-8d2c-b76d8b59e38a`.
Corrected evidence update: `524f67e5-2067-4c78-aad9-0ee842751d64`.
Corrected promotion update: `069c50a5-e7fb-47a6-9b6e-3e672acd468e`.
Read-only checks are in `evidence/verify-pipeline-promotion.sql`.
Authenticated UI observations and screenshots are retained locally in
`native_databricks/final/promotion-tests.json` and `evidence/screenshots`.

The corrected pipeline initially discovered no tables before the source edit was persisted.
The final successful update uses a source directory glob; wait for the editor's saved edit
indicator before starting a run or navigating away. Stop idle pipeline compute after
verification to free Free Edition capacity; persisted evidence remains queryable.

## Pipeline gold dashboard

Build `native_databricks/final/CutoverGuard-pipeline.lvdash.json` with
`python scripts/build_pipeline_dashboard.py`. Its main page reads persisted gold tables
from the defective and corrected evidence schemas, including counters, signed customer
effects, order drill-down, and the two gold decisions. The public retail page remains.
This is a different experiment from the earlier 50-order swap: the 50 faulty customer
mappings affect 811 orders. The owner multiset checksum need not pass after whole
customer histories are remapped; the dashboard makes no unchanged-checksum claim.


The live draft dashboard was replaced with this pipeline gold version and all three
pages were visually inspected. The main page rendered 7.5M / 811 / 50 / 0 counters,
red blocked ownership/crosswalk checks, green aggregate/quarantine checks, signed
customer bars, sorted wrong-owner order detail and defective/ready comparison rows.
Screenshots are saved locally; the dashboard was not published.
For a fresh checkout supply your own `--report`, `--retail`, and `--corrected` paths.
