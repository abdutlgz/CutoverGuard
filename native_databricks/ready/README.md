# Final dashboard template

Three pages: TPC-H orders, real public records, evidence.
The SQL uses YOUR_TPCH_RUN and YOUR_RETAIL_* table placeholders.
Generate a live definition with scripts/build_tpch_dashboard.py using your own
verified reports; do not import the placeholder template as live evidence.
The timing constant describes the original saved run, not a new measurement.

`migration-review-live.lvdash.json` is the exported definition of the published
"CutoverGuard — migration review" dashboard (pipeline gold tables, gate decision
table, sorted signed-effects chart). Import it through AI/BI Dashboards → Import;
it expects the `cutoverguard_migration_bad`, `cutoverguard_migration_fixed` and
`cutoverguard_promotion_fixed` schemas produced by the pipelines in
`databricks_demo/migration-final/`.
