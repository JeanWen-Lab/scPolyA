# Agent extensions

Install the agent wheel, then configure `native.source_root`, `native.python`
and `native.expected_commit` in an operator manifest. The source must be a
complete, clean Git checkout at the configured commit. The scientific runtime,
input data, reference and supplied model resources are configured separately.
The wheel includes the structured interfaces and differential R backend;
it does not include the complete native pipeline or model resources.

## DeepCPS native chain

Use [the example request](../examples/native-deepcps-request.json) with concrete
paths and an explicit fresh run ID. DeepCPS fields belong to `request.run`.
`deepcps` is a JSON boolean. Threads and batch size are JSON integers.
The default remains off. Inspect the native settings and plan before execution:

```bash
scpolya-operator --manifest /absolute/operator.json native settings --preset manuscript-v4
scpolya-operator --manifest /absolute/operator.json native driver -- plan --request /absolute/request.json
```

The plan binds the actual FASTA and index, supplied model manifest and model
resources, source and runtime. Show the researcher the plan's parameters and
`plan_sha256`, and run it after the researcher agrees. `run` executes exactly
that plan file; no approval file is needed.

```bash
scpolya-operator --manifest /absolute/operator.json native driver -- run --plan /absolute/job/plan.json
scpolya-operator --manifest /absolute/operator.json native driver -- status --plan /absolute/job/plan.json
scpolya-operator --manifest /absolute/operator.json native driver -- deepcps-result --plan /absolute/job/plan.json --offset 0 --limit 20
```

The reader verifies the plan, own completed attempt, inference ledger and output
identities, then returns a bounded page. DeepCPS annotates native output without
recounting, filtering sites or recalibrating scores. Model weights are
included in `models/deepcps/`.
See [the DeepCPS admission and result contract](native_deepcps_agent.md) for
resource bindings, completion seals and reader limits.

## Differential APA structured chain

Use [the synthetic request example](../examples/differential-apa-request.json)
with actual fixture paths. [The differential adapter contract](agent_differential_apa.md)
documents each request field, status and bound.

```bash
scpolya-operator --manifest /absolute/operator.json differential-apa --help
scpolya-operator --manifest /absolute/operator.json differential-apa plan --request /absolute/diff-request.json --outdir /absolute/new-plan
scpolya-operator --manifest /absolute/operator.json differential-apa execute --plan /absolute/new-plan/plan.json --outdir /absolute/new-results
scpolya-operator --manifest /absolute/operator.json differential-apa status --run /absolute/new-results
scpolya-operator --manifest /absolute/operator.json differential-apa result --run /absolute/new-results --table differential_apa_summary.tsv --offset 0 --limit 20 --max-bytes 65536
```

`plan` materializes the existing raw-count preparation and records source,
input, runtime, metadata provenance and eligibility. It writes a new directory.
`execute` wraps the reviewed `scpolya_diff` backend and runs exactly the given
plan file into a new output directory, recording the plan's file SHA256.
R and the requested inference packages must be installed separately.

Metadata must identify the actual sample, donor, condition and cell type.
Real single-donor PBMC can use `contrast: null` for preparation and eligibility
diagnostics; that plan cannot execute inference. Cells are not biological
replicates. Synthetic inference must be explicitly labelled synthetic in
provenance. These interfaces add no new statistical method.
A placeholder condition label (for example `OBSERVED`) is not a verified
biological condition or evidence for a real contrast.

The `action-binding`, `action`, `action-worker`, `rerun` and `scoped-export`
operator commands are not included in this distribution and refuse with
`operation_not_in_release`. The native and differential entries use their own
explicit checkout binding. A successful doctor check reports configuration
and file presence; it does not certify scientific dependencies or results.
