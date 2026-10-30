# scpolyA UI

Local web UI for scpolyA workspace management, run inspection, rerun planning,
parameter studies, launch supervision, retained-output previews, logs, and IGV
review.

Install the UI dependency and start it from the repository root:

```bash
python -m pip install -r scpolya_ui/requirements.txt
python -m scpolya_ui --host 127.0.0.1 --port 24120 --no-browser
```

Open `http://127.0.0.1:24120/`.

### What the UI writes

The UI's own files are kept under the UI home, `~/.scpolya-ui` (or
`$SCPOLYA_UI_HOME`):

- registries: `workspaces.json`, `jobs.json`, `run_records.json`, `studies.json`,
  the artifact index and preferences;
- launched-job records: `job_specs/<job>.json`, `job_status/<job>.json` and the
  supervisor/console log `job_logs/<job>.log`;
- notes (`annotations/`), run-evidence and reference/transcript caches
  (`igv_refs/` and similar), and the differential APA registry and
  launch logs;
- native analysis tasks under `gui-analyses/<id>/` (override with
  `SCPOLYA_UI_ANALYSIS_ROOT`).

These hold paths, aliases, notes, relocation mappings, generated commands,
configs and analysis results; the UI does not copy pipeline data. The UI does
not edit files inside an existing run root, including `state.sqlite`.

When you start or continue a run, the UI launches `python main.py run …` through
its job supervisor. That pipeline process writes into the run root exactly as a
command-line run does: outputs, `state/state.sqlite`, and
`logs/invocations/<invocation_id>/invocation.json`, which records the UI job ID
(`SCPOLYA_UI_JOB_ID`). A run with `--repair-moved-run` rewrites recorded paths in
the run root's input tables and state database (with backups). Differential APA
execution writes to the new output directory you choose.

The UI reads current structured task/reuse state from `state.sqlite`; it never
infers completion from filenames or log text. On workspace load it discovers
structured v1/v2 `invocation.json` records read-only, groups them by normalized
run root, and exposes every `invocation_id` as a distinct execution attempt.
UI-managed jobs are linked by `job_id`; refresh and restart are idempotent. The
log browser groups invocation, stage, and tool logs without changing pipeline
state. Historical execution outputs require an immutable artifact snapshot;
current shared outputs are not presented as old results. The separately labelled
imported historical annotation overlay described below records a stored file/config
association, not an execution-time snapshot or current output health.

The New run page materializes commands through the CLI parser, groups
worker/thread controls by pipeline responsibility, and can save or launch jobs
through UI-owned supervisor records. It does not expose the read-rule options
(`--count-read-rule`, `--multimapper-policy`, `--count-assignment`); use the
command line for those. Rerun previews show affected tasks and final
retained files or directories; temporary files and logs are not presented as
physical output impact.

For quantification, the New run form exposes two related settings:
`quantification_mode` (`legacy` by default) and
`intronic_misprimed_ratio_threshold` (`0.5` by default). Terminal-aware mode is
opt-in and currently unbenchmarked. The general and intronic thresholds are
independent inclusive values in `[0, 1]`; the intronic value is inactive in
legacy mode. Run previews show mode-scoped final peaks, annotations, sidecars,
counts, and summaries, while an intronic-threshold-only change in
terminal-aware mode affects the summary scope.

Registered workspace path mappings also apply to relocated invocation discovery,
log reads, and Genome browser views. Mappings require absolute path
components and use the most specific prefix; conflicting bindings and parent
traversal are refused. Unmapped relocated v2 records remain undiscovered.
The original invocation root, execution config and hash remain provenance;
only the view's layout/reference paths are mapped. Genome manifests disclose
these view paths, and the inspector shows the original root beside the mapped
view root. A relocated basename does not rename the historical native run.
Missing outputs or namespace manifests remain unavailable; mapping does not
reconstruct artifacts or establish scientific/reference equivalence.

A separately labelled historical terminal-annotation-v2 BED/GTF overlay is
available when current annotation outputs are absent. It requires one exact
succeeded invocation/config binding, one validated stored peak/annotation
namespace binding and both bounded source files. Its manifest carries the
actual file, invocation and namespace hashes. Ambiguous/incomplete bindings
are refused. This imported evidence never fills current-output missing entries
or satisfies the current annotated-output requirement in run comparison.
