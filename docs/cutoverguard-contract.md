# CutoverGuard input and report contract

## Input bundle
JSON, schema_version "1.0", data_kind "synthetic" | "historical" | "unverified".
source and candidate each contain:
- snapshot_id, captured_at (aware ISO), mapping_version
- window_start, window_end (aware ISO; start < end; interval [start,end))
- complete_through (aware ISO or null for unknown)
- profiles: [{account_id:string, customer_id:string}]
- transactions: [{event_id:string, account_id:string, amount_minor:decimal integer STRING,
  currency:"USD"|"EUR"|"GBP", event_time:aware ISO}]
config: {rule_version:"1.0", currency_scales:nonempty subset of USD/EUR/GBP,
each with integer scale 2}. Existing USD/EUR fixtures remain unchanged.
Public-retail validation may include dataset_info identifying the historical source,
controlled candidate, invoice-line measure and customer-derived receivables buckets.
This metadata does not make the controlled candidate an observed migration.
dependency_graph: {nodes:[{id,label}],edges:[{from,to}]}; optional, explicitly declared.
No floats for money; output monetary fields also decimal integer strings. Scope maximum
5000 rows per profile/transaction array; request size limit 4 MB. Preserve raw identifiers.

## Engine Python interface
cutoverguard.engine.reconcile(bundle:dict) -> report:dict
cutoverguard.engine.ReconcileError(ValueError) for invalid top-level input/contract.
Quarantine invalid records with actionable reason; do not silently erase them from counts.
cutoverguard.fixtures.fixture(kind="defective") -> input bundle dict
cutoverguard.fixtures.available_fixtures() -> list of {id,label,description}.
At least defective, clean, fixed, missing_metadata, duplicate_keys, lost_identifier,
missing_extra, compensating_amounts, mixed_currency, timezone_equivalent, malformed.

## Report (required keys)
schema_version:"1.0", engine_version:string, run_id:SHA256 canonical bundle/config/version,
data_kind, execution:{mode:"local"|"databricks", ...provenance}, input_bundle:original bundle,
status:"blocked"|"incomplete"|"ready_for_review",
summary:{primary_issue_count,impacted_account_count,quarantined_count,explanation},
checks:[{id:"identity"|"transactions"|"business",name,status:"pass"|"fail"|"unknown",
count,description}],
issues:[{issue_id,check_id,type,severity:"critical"|"warning",side:"source"|"candidate"|"both",
key,field,source_value,candidate_value,message,evidence}],
account_impacts:[{account_id,currency,source_minor,candidate_minor,delta_minor}],
currency_totals:[{currency,source_minor,candidate_minor,net_delta_minor,
absolute_account_delta_minor,affected_notional_minor}],
quarantine:[{side,table,row,reason,record}],
coverage:{source:{profile_rows,transaction_rows,in_window_rows,excluded_rows},candidate:same},
limitations:[strings], dependency_graph:declared graph.
Currency/account money aggregates requiring ambiguous/invalid input are unavailable (null)
rather than silently computed. Primary issues distinguish source/candidate mismatches
from derived business impacts. Consistent critical findings block even when evidence
is incomplete; otherwise incomplete beats pass. All implemented checks passing only
means ready for review, not compliance or an automatic production cutover.

Issue types and exact SQL/local agreement can be refined jointly by engine/cloud agents;
notify each other early. Databricks runner must execute actual reconciliation SQL and
persist source/candidate data, issue rows and impacts, not upload local results and
claim SQL calculated them. It may reuse shared deterministic input validation and
report packaging. Compare remote financial results to a local oracle before labeling
agreement verified. Never set execution.mode=databricks without successful execution
and query evidence. Unsupported remote cases should error explicitly.

## UI HTTP API
GET /api/examples -> {examples:[{id,label,description}]}
GET /api/fixture?kind=defective -> bundle
POST /api/reconcile body bundle -> report (400 {error} on invalid)
POST /api/validate-report body imported report -> validated report (400 on invalid)
Static GET /, /style.css, /app.js. New app runs 127.0.0.1:8502.
UI auto-loads defective and audits locally. Upload input JSON or import a saved cloud
report through distinct controls. Use text/escaped DOM for all supplied strings.
Keep local and saved Databricks execution labeled. Export JSON plus issue/impact CSV,
correct CSV quoting/formula-injection protection. Never recompute finance in JS; format
integer strings using BigInt or string arithmetic. Negative account deltas are not losses.
