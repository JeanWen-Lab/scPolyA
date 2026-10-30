# Native product entry (bounded bridge, not a standalone runtime)

`native_entry.py` and four instruction skills provide a bounded bridge to the
native pipeline. It does not
add a scheduler, task engine, science rule, or alternate CLI
schema. Existing native code remains the owner of planning, execution, recovery,
status, and task reuse.

## Binding and capability discovery

Always give the entry an explicit complete checkout:

```bash
PY=/path/to/scientific/python
SRC=/absolute/sc-polya-checkout
"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" bridge \
  --analysis-skill /absolute/scpolya-analysis/SKILL.md
```

`bridge` refuses `incomplete_native_source_root` when required repository-root
files are absent. This is deliberate: copying only `scpolya_agent/` does not
make `native_task_driver` standalone, because it imports repository-root
`arguments`, `task_manage`, and `scpolya_ui` modules. No private host path
is used as this entry's source default.

A successful bridge includes the driver request/resource keys, allowed verbs,
required-file pins, and full native `runtime_identity` (interpreter/tooling plus
tracked source bytes and status). Its `product_operations` field reports
`binding: not_included_in_release` with an empty operation list: case-study
evidence operations are not part of this distribution. An optional external
`scpolya-analysis` skill can be routed only by explicit `--analysis-skill PATH`
and only when its SHA-256 equals the pinned `ANALYSIS_SKILL_SHA256` in
`native_entry.py`; otherwise it is reported as unavailable. It is never modified.

## Actual settings and semantics

```bash
"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" settings --preset manuscript-v4
```

The response contains actual parser defaults and the selected native preset.
For the frozen parser and preset, `minpolyA` defaults to 10 and the manuscript
preset is 5; `minMAPQ` defaults to 0 and the manuscript preset is 10. Explain
`minpolyA` as total terminal A/T length, not homopolymer count, and a poly(A)
depth threshold as per-base coverage, not tail-read cluster count. Multi-site
genes and genes with multiple eligible terminal sites remain distinct.

## Driver routing and authorization

```bash
"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- plan --request ABS_REQUEST
"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- status --plan ABS_PLAN
"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- run --plan ABS_PLAN
"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- recover --plan ABS_PLAN
```

The driver verbs `plan`, `run`, `recover`, `status`, `stop`,
`harvest`, `snapshot`, `identity`, and `deepcps-result` are forwarded. A plan must
already exist for run/recover; this entry never mints one. Run/recover execute
exactly that plan (its `plan_sha256` is verified and recorded). No approval file
is needed: the agent shows the researcher the plan's parameters and hash in the
chat and runs it after the researcher agrees. A legacy `--approval PATH`
argument is accepted and ignored. Plan binding is integrity binding only, not an
OS security/isolation claim. Status must come from native
state; job IDs, exit codes, logs, and file presence do not establish verified
completion.

## Four instruction skills

- `skills/scpolya-prepare-run/SKILL.md`
- `skills/scpolya-compare-settings/SKILL.md`
- `skills/scpolya-resume-diagnose/SKILL.md`
- `skills/scpolya-explain-export/SKILL.md`

They instruct discovery, explicit settings, native planning, showing the
researcher the exact plan parameters and hash before running, honest
unavailable evidence, and standalone-GUI handoff. They do
not authorize writes or replace code boundaries.

## Bounded tests

```bash
cd "$SRC"
python -m pytest -q tests/test_native_product_entry.py
```

The tests use temporary files and CLI/help-level checks only. They do not run a
native job or hash a scientific payload. A copied-package-only source must fail
before driver import.

## Successor admission and process isolation

The entry checks native plan schema/hash before importing the selected driver. The selected physical checkout must equal
`plan.product.repo_root`. Its independently observed Git head/index/tracked-byte
fingerprint/status must equal the existing native `runtime_identity.source`, and
its tracked driver must reside at that root. The native runtime identity digest
and plan argv/Python binding are checked as well. No attempt is created by this
entry. The native driver validates full runtime/input identity,
and quiescence, and owns every supervisor action.

Use the plan's Python interpreter. A moved or different checkout is refused;
relocation requires an explicitly reviewed plan/rebind, never silent substitution.
Source hashing in this admission checks checkout files, not scientific inputs.
Existing native plans are not rewritten. Current layout/hash/tool observations
are not external trusted pins or dependency/scientific certification. The native
identity does not hash contents of already-untracked files. This is not an
OS security boundary against concurrent filesystem changes or malicious code.

Public bridge/settings inspection and driver routing use fresh isolated Python
processes. `python -B -m scpolya_agent native-entry ...` dispatches to this entry
through the same fixed subprocess, preserving stdout/exit status and avoiding
imported module reuse across checkouts. Private inspection helpers additionally
refuse foreign cached imports.

`driver -- run --help` and `driver -- recover --help` are read-only native help
and need no plan file. Actual actions accept both `--plan PATH`
and `--plan=PATH` syntax; duplicate/unknown arguments are refused before import.
Missing source, plan, malformed JSON and child transport failures return structured
entry errors. Existing native DriverError JSON codes are preserved.

`plan` writes a plan and performs native input preflight, including BAM hashing
by default; it is not catalog discovery. `stop` kills an attempt, `harvest` writes
resource evidence and stops/resets a retained unit, and `snapshot` writes a SQLite
backup/summary. Use those actions only when the researcher asked for them or agreed in the
chat; they do not start a new run. No scheduler is created by the bridge.
