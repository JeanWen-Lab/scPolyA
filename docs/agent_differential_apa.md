# Structured differential APA operator adapter

`python -m scpolya_agent.differential_apa` provides `plan`, `execute`, `status`,
and `result`. The installed operator routes `differential-apa` through its clean,
commit-pinned native checkout and configured isolated Python. The scientific
implementation remains `scpolya_diff.prepare` and `scpolya_diff.cli._run`;
[the scientific contract](differential_apa.md) defines counts, denominator,
distal ties, unavailable values, endpoints and hypothesis families.

Planning **materializes raw-count aggregation**, using the existing prepare
function, in a new exclusively owned `plan/prepared` directory. It reads the
explicit sources and writes preparation diagnostics, an expanded request,
input/source/Python hashes and eligibility into `plan/plan.json`. It performs no
inferential fit. Every plan and execution output
directory must be absent; empty existing directories are also refused. Failed
preparations/plans and failed execution attempts remain available. Use new paths
for any externally reviewed retry; no automatic retry or overwrite is provided.

A request is an object with this format (all input/runtime paths are absolute):

```json
{
  "schema": "scpolya.differential_apa.request.v1",
  "inputs": {
    "manifest": "/task/manifest.tsv",
    "metadata": "/task/metadata.tsv",
    "annotation": "/task/common.tsv.gz"
  },
  "cell_type": "CD8 T",
  "provenance": {
    "kind": "real",
    "description": "Observed source cohort and catalogue provenance",
    "metadata_origin": "Source record establishing donor, condition and cell labels"
  },
  "contrast": {"control": "CTRL", "case": "CASE"},
  "runtime": {
    "rscript": "/usr/bin/Rscript",
    "r_library": "/task/rlibrary"
  },
  "options": {
    "analysis_set": "native-terminal",
    "min_cells": 20,
    "allow_unmatched_cells": false,
    "methods": "edger,limma",
    "descriptive_only": false,
    "paired": false,
    "batch": false,
    "min_pas_count": 5,
    "min_pas_samples": "auto",
    "min_unit_count": 20,
    "seed": 20261021,
    "timeout_seconds": 1200
  }
}
```

All options shown are defaults. Unknown options, duplicate JSON keys, invalid
boolean/integer types and nonfinite values fail. `min_pas_samples` is `"auto"`
or a positive JSON integer. `provenance.kind` must be `real` or `synthetic`;
source labels and provenance descriptions are required, but this adapter cannot
independently authenticate their biological truth. Never split one donor's cells
into artificial donors or invent condition labels.

Set `contrast` to `null` (or omit it) for a single observed condition. Runtime may
then be omitted. The outcome is `PREPARED_ELIGIBILITY_ONLY`; it reports observed
conditions, actual count-qualified pseudobulks and donor counts, and cannot be
executed as a contrast. This is the appropriate interface for the local
single-donor PBMC `OBSERVED` input. Its existing donor identity and condition
remain unchanged. No second group, inference or p-values are created.

For a real contrast, preflight uses prepared matched cells and the actual
surviving donor pseudobulks, complete pairs, categorical-design rank and residual
degrees of freedom. It checks recorded batch/condition confounding, the declared
PAS support gate, and the unit's >=2 PAS/per-pseudobulk raw-count gate. It emits
counts and reasons, never p-values. Insufficient donors select descriptive mode;
this still requires an actual R runtime, but not inferential packages. Length
availability, empirical-Bayes estimability and final statistical validity remain
backend decisions. Metadata labels alone never imply that a unit is testable.

Contrast plans probe the explicit Rscript in vanilla R with single-thread numeric
libraries. Requested namespaces must load; modern edgeR must expose the reviewed
API. Hash bindings include the Rscript, R executable/shared library, versions and
all loaded namespace trees (including dependencies). No packages are installed.
Missing runtime/packages produce `BLOCKED_RUNTIME`. Invalid metadata/design
produces `FAILED`; no units after inferential coverage gates produce
`BLOCKED_ELIGIBILITY`. Only `READY_FOR_EXTERNAL_APPROVAL` permits execution (the
status name is kept for compatibility; it means "ready to execute" and no
approval file is involved).

```bash
python -m scpolya_agent.differential_apa plan \
  --request /task/request.json --outdir /task/plan
```

Before executing, show the researcher the frozen plan's contrast, cell type,
methods, inputs, output directory and the plan file's SHA256, and execute only
after the researcher agrees. No approval file is needed; `execute` runs exactly
the plan file given and records its SHA256 in `attempt.json`. Pass
`--plan-sha256 HASH` (the hash shown to the researcher) to refuse execution if
plan.json changed since it was shown; the GUI always does this. A legacy
`--approval` argument is accepted and ignored.

```bash
python -m scpolya_agent.differential_apa execute \
  --plan /task/plan/plan.json --plan-sha256 SHA256_SHOWN_TO_THE_RESEARCHER \
  --outdir /task/attempt-001
python -m scpolya_agent.differential_apa status --run /task/attempt-001
python -m scpolya_agent.differential_apa result --run /task/attempt-001 \
  --table differential_apa_summary.tsv --offset 0 --limit 20 --max-bytes 65536
```

Execution verifies output ownership, wrapper/scientific source/Python,
all source/prepared files, manifest path resolutions and R runtime bindings
before creating the attempt. The original request is a provenance receipt; the
normalized request frozen inside the plan controls execution. The plan
must remain beside its original prepared directory. Source files and runtime
bindings are checked again after R. Execution records the backend's exact status,
including `DESCRIPTIVE_ONLY` and warning/untestable-length statuses. Exceptions
preserve `FAILED`, error text and any backend artifacts. No unconditional success
sentinel is generated. A killed caller may leave recorded `RUNNING`; that record
is not a claim that a worker remains alive, and is never automatically retried.

`status` checks the completed output inventory and every output hash, plus backend
completion/manifest consistency. It does not need the original raw inputs to
remain mounted. `result` requires verified backend completion and a known TSV
name; it rejects traversal and serves exact string values, including `NA`, without
zero-imputation. Pages contain at most 1000 rows, offset <=1,000,000 and <=262144
serialized JSON bytes (default 20 rows/65536 bytes). `next_offset` is null at EOF.
The byte budget covers the returned JSON object, excluding the CLI newline. A
single row/header too large for the requested budget is refused explicitly.
Result-table identity/hash is returned and rechecked after the read.

Bound each invocation externally to <=2 CPU, <=4 GiB RAM and swap=0. This adapter
records that caller resource contract; it does not create a systemd scope or
claim to enforce CPU/memory itself. Input caps are 128 manifest libraries, 2 GiB
of unique source-file bytes, 200000 metadata rows and 2 MiB per JSON document;
R execution timeout is <=1200 seconds and runtime probing <=45 seconds. Gzip
expansion/count aggregation still requires the caller's memory/time envelope.
A synchronous call can be run in the existing supervised job infrastructure;
this adapter does not add a scheduler. Status/result calls are read-only.

The bounded synthetic verification is:

```bash
SCPOLYA_AGENT_DIFF_ACTUAL_R=1 python -m pytest tests/test_agent_differential_apa.py -q
```

Actual R tests use the task-specific library identified by
`SCPOLYA_DIFF_R_LIBRARY` (or the existing local validation library default).
Synthetic inference validates transport and arithmetic; it does not validate
real multi-donor inference, population generalization or FDR calibration.
