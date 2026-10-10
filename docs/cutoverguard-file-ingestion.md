# Public-retail file ingestion — actual execution verified

User Terminal retail-run-002 completed successfully after the uploader directory
fix. All 1,467 source and 1,467 candidate invoice lines, profiles and manifest were
read back exactly; SQL findings, account effects and currency totals matched the
full local oracle. Twelve statement IDs are saved. Root checked the actual report,
CSV hashes and local import recalculation. Report: databricks_files/retail-run-002/report.json;
compact evidence: verification-summary.json in that directory. Review status is
blocked as expected for two controlled ownership breaks, distinct from verified
execution. GBP totals £32,293.03 on both sides, absolute account differences £77.56,
affected transaction notional £69.38; these are invoice values, not cash or losses.
The older prepared/run-001 notes below describe history, not current execution status.

This is a bounded, prepared Databricks file-ingestion demonstration.
The existing ownership-swaps case contains **1,467 historical invoice lines per side**.
Its candidate contains deliberately controlled ownership defects. Customer-derived
receivables buckets are not bank accounts; invoice-line values are not observed
cash or settlement. The original extraction metadata and source-file fingerprint
remain in the manifest and report.

The new path uploads CSV files to an **existing Unity Catalog Volume**, creates
six unique Delta tables, and executes the existing platform reconciliation SQL.
It does not submit source rows as SQL `VALUES`, upload a local report as a result,
overwrite an existing table/file, grant privileges, publish anything, or create
accounts. Profiles keep string identifiers; amount_minor becomes DECIMAL(38,0);
event time and window boundaries become TIMESTAMP once during ingestion. Imported
timestamps are checked as exact UTC microseconds, independent of formatting and
session timezone. Every imported row, including rows outside the window, is checked
against the prepared input. Every primary finding, account effect and currency
total is then compared with the complete local oracle.

## User Terminal command

First obtain an existing Volume name with READ VOLUME and WRITE VOLUME permission,
plus USE CATALOG/SCHEMA. Target tables require CREATE TABLE permission. The selected
SQL warehouse must support Unity Catalog `read_files`. Existing warehouse access
alone does not prove Volume access. Replace `your_existing_volume` below with the
actual name; **no Volume with that name is assumed to exist**.

```sh
.venv/bin/python scripts/run_cutoverguard_files.py \
  --warehouse-id YOUR_WAREHOUSE_ID \
  --volume workspace.default.your_existing_volume \
  --output databricks_files/retail-run-001 \
  --execute
```

Without `--execute`, the command only exports local CSV, input JSON, SQL and state.
It creates no remote resources. To execute a prepared directory, or resume after
completed stages:

```sh
.venv/bin/python scripts/run_cutoverguard_files.py \
  --output databricks_files/retail-run-001 --resume --execute
```

The profile, warehouse, generated table names and unique upload directory are
checkpointed. `--profile NAME` can be provided on initial preparation. Completed
writes are skipped on resume; imported data and calculations are always rechecked.
A timeout or failed mutation with an unknown outcome **blocks automatic retries**.
Inspect that unique path and query history, then use a fresh output directory for
a new attempt. No cleanup or deletion is automated.

Successful execution writes `report.json` with `execution.mode=databricks`, all
statement IDs, file hashes, imported-row agreement, SQL result rows and full oracle
agreement. `state.json` records failures and ambiguous stages. A retained earlier
report is an exported snapshot of its successful run; later failed revalidation
marks the checkpoint unverified and does not erase that prior evidence.

## Scope and evidence

Local preparation at `databricks_files/retail-prepared` used the illustrative
Volume name `workspace.default.cutoverguard_files`. Its existence is unverified;
prepare a fresh directory with your actual Volume name before execution.
That preparation was not execution evidence. Actual retail-run-002 used the existing
workspace.default.workshop Volume and passed complete imported-row and SQL checks.
This is a 1,467-line extract, **not a million-row scale benchmark**. The complete
oracle remains bounded by the existing 5,000-row per
array contract. Timestamp-mismatch findings are outside this ownership-swap demonstration and
are rejected before upload; typed SQL string rendering is not accepted as timestamp
mismatch evidence. Duplicate keys, quarantined rows, incomplete metadata and amounts
outside the SQL safety bound are explicitly rejected rather than silently cleaned.

Platform syntax sources: [read_files SQL function](https://docs.databricks.com/aws/en/sql/language-manual/functions/read_files)
and [files in Unity Catalog Volumes](https://docs.databricks.com/aws/en/volumes/volume-files),
checked October 10, 2026. The Databricks CLI uses `fs cp LOCAL dbfs:/Volumes/...`;
`read_files` receives an explicit schema and CSV FAILFAST settings.

## Discover or create a managed Volume

List visible Volumes without changing resources (this may start SQL compute):

```sh
.venv/bin/python scripts/setup_cutoverguard_volume.py \
  --warehouse-id YOUR_WAREHOUSE_ID --list
```

Visibility does not itself verify file write permission. If no suitable Volume
exists and your identity has CREATE VOLUME on the schema, create a fresh managed
Volume with a collision-resistant name:

```sh
.venv/bin/python scripts/setup_cutoverguard_volume.py \
  --warehouse-id YOUR_WAREHOUSE_ID --create \
  --output databricks_files/volume-001.json
```

Use the exact printed three-part name as ingestion `--volume`. The helper creates
no catalog/schema, adds no grants, and never drops/replaces anything. A creation
failure saves the intended name and statement IDs. Read-back recovery is safe:

```sh
.venv/bin/python scripts/setup_cutoverguard_volume.py \
  --warehouse-id YOUR_WAREHOUSE_ID --resume \
  --output databricks_files/volume-001.json
```

Recovery only runs DESCRIBE VOLUME; it never repeats CREATE. If the Volume is
absent, inspect query history and choose a fresh checkpoint for a new creation.
Official references: [CREATE VOLUME](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-volume),
[SHOW VOLUMES](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-volumes),
[DESCRIBE VOLUME](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-describe-volume),
checked October 10, 2026.

## Upload parent directory and safe diagnostics

The uploader now runs `fs mkdir` for the strictly validated, uniquely generated
run directory before copying CSV. It then lists that directory as JSON and refuses
an already-present target CSV. Copy never uses `--overwrite`. Failed mkdir or an
unrecognized/failed listing prevents copy. Errors report fixed categories only:
`authentication`, `permission_denied`, `directory_or_path`, `existing_file`,
`network`, or `unknown`; raw CLI stderr is never printed or saved.

This fixes a missing parent-directory preparation step in the code. It does not
establish the cause of a previous live upload failure. Existing runs stopped with
`attempted=upload_manifest` keep their ambiguous checkpoint and cannot silently
resume. Inspect the existing Volume and run path with the read-only diagnostic
script, then prepare a **fresh output directory** for a new attempt after any
permission/authentication issue is resolved. Do not manually clear the attempted
stage or reuse a file merely because it appears in a listing; listing is not a
content-hash verification.

CLI syntax was checked against local `databricks fs mkdir --help`, `fs cp --help`,
and the official [fs command group](https://docs.databricks.com/aws/en/dev-tools/cli/reference/fs-commands)
on October 10, 2026. mkdir is idempotent for an existing directory; the upload
file itself must not already exist.
