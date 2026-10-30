---
name: scpolya-prepare-run
description: Prepare and execute a native scPolyA run by validating a complete source binding, resolving parser settings, building the native plan, showing the researcher the exact plan parameters and plan hash, and running that plan through native_task_driver after the researcher agrees in the chat.
---

# Prepare and run a native scPolyA analysis

Use the explicit interpreter and checkout paths supplied for this run:

```bash
PY=/absolute/python
SRC=/absolute/sc-polya-checkout
```

Start with `"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" bridge`. Treat layout and current hashes as observations,
not external trust pins or dependency certification. Runtime discovery requires
a tracked source digest and observed tool availability. A directory containing only `scpolya_agent` is not a
complete native checkout; stop and request the explicit complete source root
instead of importing from an ambient path.

Call `"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" settings --preset manuscript-v4` before choosing values.
State the preset explicitly and distinguish parser defaults from preset values.
`--minpolyA` is the minimum total terminal A/T length in the called tail
context, not a homopolymer count. A poly(A) depth threshold is per-base tail
coverage, not the number of tail reads in a cluster.

Prepare an absolute native request and let `"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- plan --request ABS_REQUEST` perform BAM preflight, parser materialization, task preview, and
plan construction. Never invent a plan, request schema, task graph, or
completion marker. A blocked plan is not ready: fix what you can (paths,
settings, scope) and build a new plan; otherwise explain the native issues to
the researcher. A plan with status `ready_for_external_approval` is ready to run (the
status name is kept for compatibility; no approval file is involved).

Before running, show the researcher in the chat, in plain language, the exact
parameters of the plan file you built: scope (samples, BAMs, contigs), the
preset and key settings with their values, resources, the output/run root,
and the `plan_sha256`. Take these values from the plan file itself, not from
memory of the request. Ask whether to run it, and run only after the researcher
says yes. If anything changes, build a new plan and show it again.

The selected checkout and tracked driver/source identity must equal the plan
before any native driver is imported or attempt is created. Run exactly the
plan you showed:
`"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- run --plan ABS_PLAN`.
The driver verifies the plan hash, runs only that plan, and records its
`plan_sha256` in the attempt. No approval file is needed.
Resource values in a request are native request values, not proof that an
external launcher enforced a quota.

After submission, use native status. Never infer completion from a returned
job ID, process exit, or file existence. Wait for the driver's verified
terminal state and inspect its refusal or failure details before comparing or
exporting anything.
