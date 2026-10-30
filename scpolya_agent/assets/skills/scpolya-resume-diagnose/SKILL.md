---
name: scpolya-resume-diagnose
description: Diagnose native scPolyA task and supervisor state through the existing driver, then recover the same plan after the researcher agrees in the chat, without treating files or logs as completion.
---

# Resume and diagnose through the native owner of state

Use the explicit interpreter and checkout paths supplied for this run:

```bash
PY=/absolute/python
SRC=/absolute/sc-polya-checkout
```

First bind the complete source checkout with `"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" bridge`. For an
existing job, request native state with `"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- status --plan ABS_PLAN`.
Use the returned supervisor state, native quiescence, attempt lineage, failure
details, and verified task state. A submitted job ID, nonzero/success process
return code, log text, or expected output file does not establish success.

Keep `not_launched`, `running`, `lost`, `unknown`, `stopped`, `failed`,
`succeeded_unverified`, and `succeeded_verified` distinct. If state is unknown,
or the failure class is not documented, explain the evidence to the researcher
and propose a repair rather than applying a guessed one. Do not edit SQLite state, completion markers, task hashes, or
plans.

Resume only the same verified plan. Before recovering, tell the researcher in
the chat what will be resumed: the plan path, its `plan_sha256`, the run root,
the last attempt state and failure details, and the plan's key settings and
resources (unchanged from the original plan). Recover only after the
researcher says yes:

```bash
"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- recover --plan ABS_PLAN
```

This reuses the native recovery path for the whole plan; do not ask again for
every task. No approval file is needed. Never change
scientific parameters to make a failed run finish. Reduced concurrency is not a
universal memory repair. For diagnostics, the driver snapshot operation is an
operator action over an existing plan; it is not a resume and not a success
claim.

`stop` kills an existing native attempt; `harvest` writes evidence and stops/resets its retained unit; `snapshot` writes a SQLite backup and summary. Use those only when the researcher asked for them or agreed in the chat. They do not imply success or start a new run.
