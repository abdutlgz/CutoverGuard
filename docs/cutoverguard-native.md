# Native dashboard and Genie

Prepared payloads contain four counters, signed account effects, three comparison tables and a linked Genie space. Original project resources were created and SQL/configuration checked; those private run checkpoints are excluded here. Rendering and model answers require human inspection. This package does not publish or grant access.

In your own authenticated workspace, execute the synthetic defective/fixed runners first (see [platform workflow](cutoverguard-databricks.md)), then use explicit report paths and a fresh output directory:

```sh
.venv/bin/python scripts/setup_cutoverguard_native.py --help
.venv/bin/python scripts/setup_cutoverguard_native.py --prepare-only --output native_databricks/new-run
```

Inspect prepared requests and use the creator's --create options with your warehouse and report paths. Fresh creation validates all five SQL datasets against the exact oracle. Recovery --resume requires your own saved state; never assume the original author's run exists. Ambiguous creation outcomes block retries until existing IDs are recovered. Counter updates require that same saved state, validate SQL and draft content, and use an ETag. No publish operation is provided.

Genie instructions distinguish synthetic snapshots, supplied completeness declarations, currencies, exact units and discrepancies from losses. Ask the four prepared questions and inspect SQL/results. Benchmark configuration does not prove answer quality. Ready for review is not migration approval.
