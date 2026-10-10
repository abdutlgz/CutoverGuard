# Seeded synthetic defect experiment

Seed: 20261010; rows per source ledger: 100; trials: 700.

Counts below describe this designed experiment only. They are not real-world accuracy estimates.
Exact count fractions are authoritative; percentages are rounded to two decimal places.

| Category | Trials | CutoverGuard detected | Baseline detected | Exact primary match | Clean false positives (CG / baseline) |
| --- | ---: | ---: | ---: | ---: | ---: |
| ownership_swap | 100 | 100/100 (100%) | 0/100 (0%) | 100 | N/A (defect trials) |
| compensating_pennies | 100 | 100/100 (100%) | 0/100 (0%) | 100 | N/A (defect trials) |
| missing_extra | 100 | 100/100 (100%) | 0/100 (0%) | 100 | N/A (defect trials) |
| same_day_timestamp | 100 | 100/100 (100%) | 0/100 (0%) | 100 | N/A (defect trials) |
| duplicate_key | 100 | 100/100 (100%) | 100/100 (100%) | 100 | N/A (defect trials) |
| single_amount_change | 100 | 100/100 (100%) | 100/100 (100%) | 100 | N/A (defect trials) |
| clean | 100 | N/A (control) | N/A (control) | 100 | CG 0/100 (0%); baseline 0/100 (0%) |

Experiment fingerprint: `ed7d6f9b083d1cc09d514fce180f39a58219b4ebc5d03b3456119147d123b471`

## Limitations

- Designed synthetic defect sensitivity, not real-world detection accuracy, migration failure prevalence, or savings.
- A totals/checksum baseline is illustrative; existing production reconciliation tools may already check event and ownership parity.
- Clean-control false positives require critical findings; incomplete evidence alone is recorded separately, not counted as detection.
- Timing, GPU acceleration, Databricks execution and statistical population inference are outside this experiment.
- Baseline checks aggregate counts, separate currency/UTC-day totals and an account-multiset checksum; they do not prove individual event parity.
