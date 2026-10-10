# TPC-H-first dashboard and migration gate

The initial publication commit is `0074d3e`. Versioned intermediate output folders
are excluded from the public repository; private recovery checkpoints are archived.
Only final generated templates belong in `native_databricks/ready`.

## Dashboard

```sh
.venv/bin/python scripts/build_tpch_dashboard.py --verify-sql
```

Defaults use the author's verified scale report and paired historical-retail reports.
On a fresh checkout, execute those workflows first and pass your saved paths with
`--report`, `--retail`, and `--corrected`. The generator requires isolated, completed,
verified evidence and matching warehouses. It writes one stable output directory:
`native_databricks/final/CutoverGuard.lvdash.json`. Import this file through AI/BI
Dashboards → Import dashboard. It has three pages:

1. **7.5 million orders:** counters, five distinct aggregate checks, signed customer
   effects and wrong-owner order drill-down, largest price first.
2. **Real public data:** historical UCI records and corrected replay, using GBP.
3. **Evidence:** fingerprints, attribution, timing boundary and limitations.

The first page queries existing isolated TPC-H tables. The new daily-total check
is verified by `--verify-sql`, alongside exact counters, signed customer effects
and order-level drill-down. No tables are created. Compute may start. A successful
check records `scale-query-verification.json`; inspect rendering after import.

TPC-H price units have no currency. The counter near 60 seconds is the sum of the
recorded per-query CLI submission/poll/fetch intervals from the original run;
discovery is separate. It is not engine-only runtime or a speedup claim.

## Lakeflow migration experiment

Creation of an empty serverless pipeline was confirmed in the user's workspace.
This confirms creation access, not successful compute, SQL compilation, expectation
behavior or a promotion block. Those need an executed defective/fixed pair.

```sh
.venv/bin/python scripts/build_migration_gate.py
```

This prepares `databricks_demo/migration-final/defective.sql` and `corrected.sql`.
Use `--report` for your own verified scale report. Only the customer crosswalk
expression differs between the two variants.

The legacy extract is the frozen 7.5M-order snapshot. A separately retained identity
crosswalk defines the expected customer relation. The migration crosswalk swaps
25 customer pairs. **Every order for those customers** is remapped, so the result
is not the previous 50-order experiment and must not inherit its finding count.
TPC-H has integer customer keys; this is a swapped-crosswalk demonstration,
not an observed leading-zero collision or a real legacy migration.

Bronze retains the legacy extract and both crosswalks. Silver types amounts as
DECIMAL, interprets DATE at UTC midnight, checks keys and fields, retains invalid
rows in explicit quarantine tables, and excludes them from the valid silver views.
Gold performs keyed ownership/amount/date reconciliation, customer allocation
aggregation and separate crosswalk checks. Its decision also checks quarantine,
nonempty input, duplicate crosswalk keys, completeness counts and total parity.
Legacy row count, total and owner/order checksum expectations are pinned to the
saved verified extract manifest, so silently shrinking the legacy extract cannot
pass by shrinking both sides. These aggregate manifest checks are not a
cryptographic authentication of every source row; the frozen legacy extract remains
the trusted reference for keyed comparison.

The `cutover_gate` materialized view has a readable expectation:

```sql
CONSTRAINT no_wrong_owner_promotion
EXPECT (status = 'ready') ON VIOLATION FAIL UPDATE
```

`promoted_orders` explicitly depends on `cutover_gate`. A triggered pipeline may
update independent diagnostic flows when the gate fails. The gate is not a global
rollback, a production switch, a BNY integration or complete financial approval.
A previously successful promotion can remain available after a later failed
update; do not describe stale data as the bad candidate having been promoted.

### Deploy and verify

1. Use one empty development pipeline; do not create another. Keep it **serverless**.
   Enable the expectation-supporting edition if the workspace exposes that setting.
2. Set its default catalog to `workspace` and a **new dedicated schema**, for example
   `cutoverguard_migration_bad`. Never use production or the existing shared schema.
3. In the blank transformation editor choose **SQL** and paste `defective.sql`.
   Keep only this active transformation (do not include both variants).
4. Run the pipeline. Save the update ID and inspect the actual error. The desired
   error must name `no_wrong_owner_promotion`, not permissions or SQL compilation.
   Inspect `gold_cutover_decision`, `gold_order_issues`, quarantine and customer effects.
   Confirm no newly promoted bad orders exist. A generic red run is insufficient.
5. Save the defective evidence first. Change to a second fresh schema such as
   `cutoverguard_migration_fixed`, replace the active SQL with `corrected.sql`, and run.
   Verify zero findings/quarantine, unchanged counts/totals, `ready` and the full
   promoted row count. Do not reuse the original 50-order count as an expectation.
6. Capture both runs. Describe the pipeline as prepared until these checks pass.
   If serverless/expectations fail, retain the verified dashboard demonstration;
   do not claim a job fallback exists unless it has been built and executed.

[Databricks expectations](https://docs.databricks.com/aws/en/ldp/expectations)
confirm fail-update behavior and the distinction between a failing flow and parallel
flows. [SQL pipeline development](https://docs.databricks.com/aws/en/ldp/developer/sql-dev)
documents materialized views. Checked October 10, 2026.
