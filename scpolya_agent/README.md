# Bounded tools for scPolyA agents

The package provides three separate operations. Each returns evidence for its
declared scope; none schedules a pipeline or chooses scientific parameters.

| Operation | Input | Output |
| --- | --- | --- |
| `inspect_workspace` | Explicit run root | Bounded saved configuration, completion and provenance observations |
| `diagnose_gene_units` | Parsed counts, catalogue, unit registry and policy | Descriptive gene-unit denominators and metric recipes |
| `diagnose_native_gene_selection` | Explicit native files, hashes, namespace and selected gene/cells | Source-bound diagnostics and native distal-PSI comparisons |

## Run-context inspection v1

```python
from scpolya_agent import encode_inspection, inspect_workspace
context = inspect_workspace("/explicit/output/run-id")
wire_json = encode_inspection(context)  # Same encoding as CLI, without newline.
```

```bash
python -B -m scpolya_agent inspect --run-root /explicit/output/run-id
```

The CLI emits one JSON document on stdout. Exit 0 means the inspection returned
context (which can be partial); exit 2 means an invalid invocation/root or an
unexpected failure. `python -m scpolya_agent --help` also returns JSON. The schema
identifier is `scpolya.agent.workspace_inspection.v1`. Unknown values are null,
not zero. The inspector does not execute a pipeline, recommend parameters,
perform scientific analysis or export a report.

The optional `--repo-root` records additional checkout provenance; it cannot
replace the actual inspector module's checkout provenance or establish a
historical run version. Git is invoked with a fixed argv, optional index writes
disabled, and a five-second timeout. There is no caller-supplied command API.

## Evidence

* `versions.inspector_checkout`: actual reader checkout commit and dirty flag.
  Existing run schemas do not persist a verified product version/commit, so
  `versions.run_product` is unavailable. A cache epoch is not a product version.
* `configuration`: saved `run_config_ledger:full_config`, or the core reader's
  legacy `meta:config_json` fallback. The saved values are returned without
  merging current defaults, rebuilding CLI arguments or resolving fresh inputs.
  No claim is made that an incomplete saved object contains all execution args.
* `cache_epoch`: original-run-root compatibility is assessed only partially.
  `copied_state_epoch` contains a bounded summary of the core epoch report over the private
  DB+WAL copy, labeled `assessment_scope: copied_state_only`; it cannot determine
  whether the original filesystem contains untracked output. Its source DB path
  is provenance, not the location examined by the core reader. Filesystem path
  examples are null because that evidence was not collected for the original.
  At the outer level, a blocking condition in copied state yields `status:
  blocked` and `blocks_run: true`. All other cases, including `fresh`, `current`,
  missing state, and a root with no observed output, yield `status: unknown` and
  `blocks_run: null`. An input budget can also withhold the copied report.
  `assessment_complete` is always false. There is no false
  (clear-to-run) conclusion. The adapter does not scan/reimplement native root
  compatibility rules, copy/link scientific directories, or authorize resuming.
* `task_completion`: existing core v2 structured completion snapshot and
  provenance. Legacy-only, malformed or missing authority stays unavailable;
  logs, file existence and invocation lifecycle are not completion authority.
  This snapshot lists completed coordinates; it is not a reconstructed queue,
  dependency graph or estimate of pending work.
* `identities`: the core strict finalized-unit CSV parser selected by persisted
  dedup method, preserving textual IDs such as `001`, `NA`, `1e3` and `S.chr1`.
  No unit-ID splitting, filename guessing or pandas numeric coercion is used.
* `artifacts.native_locations`: stat-only observations of fixed native output
  directories, even if state is missing. Directory existence says nothing about
  its contents, readiness, producer completion or scientific validity.
* `artifacts.projection`: existing core output projection over saved config,
  only for a current copied-state cache epoch and a matching configured root.
  The raw `output_dir` must not contain a `..` component; `run_id` must be a
  nonempty string unchanged by native `RunLayout.clean_name`, and cannot be
  `.` or `..`. The actual native `RunLayout.Root` is checked for equivalence
  before filesystem reads, and that layout supplies namespace preflight paths.
  Ambiguous/changed bindings are unavailable; persisted config is not rebased
  or rewritten to make a projection fit the requested root.
  This condition does not imply original-root compatibility. This is a
  current-reader path projection, not a historical result/config snapshot. Glob
  templates are returned without expansion. Before any native namespace reads,
  manifest and namespace ancestry are checked without following links and
  immediate namespace entries are counted against a shared limit. Literal paths
  are checked component by component before lstat; escaping/symlink paths are
  unresolved without accessing their targets. No BAM contents are read.
* `artifacts.completed_task_health`: withheld for every branch. The combined
  native health API accesses artifact targets before returning expectations;
  some branches can also create directories. Until a safe expectation-only API
  exists, no native evaluator is called. Bounded returned completion coordinates
  are listed as unassessed with a reason. `entries` is null, and `done_task_count`
  is null when a completion snapshot was unavailable/omitted. Path observations
  remain file/directory metadata only; they do not establish completed-task
  readiness, marker validity or scientific quality.
* `invocations`: only bounded `logs/invocations/*/invocation.json` metadata is
  read. v2 config snapshots require schema 1, a matching SHA-256 over canonical
  JSON, and matching root/output identity. v1 records keep snapshot unavailable,
  even if they contain unverified config-like fields. These records are not
  promoted to task completion or reconstructed from the current state/config.
  The inspector validates the config evidence, not full log lifecycle integrity.
  `invocation_catalog` gives observed immediate entry count, returned record
  count, omitted entry count, bytes actually read, and returned verified snapshot
  count (`snapshots_available`). This is not the total number of valid snapshots
  on disk when some records were not read. Above the entry limit, total is null
  with a lower bound. A returned row may have an omitted/unavailable config.
* `scientific_quality`: explicitly not evaluated. No statistics, full BAM hashes,
  DeepCPS availability, independent atlas truth or biological conclusions are
  produced.

## Read behavior and bounds

No SQLite connection opens the original database. The adapter copies at most
64 MiB of DB plus WAL evidence to a private `/tmp` directory, preserving WAL
instead of silently ignoring committed WAL transactions with `immutable=1`.
Existing core readers can create SQLite coordination files in that disposable
directory. Symlinked state and orphan sidecars are rejected by the core safety
reader. Nonempty rollback journals, oversize evidence, and source stat changes
during copying produce structured unavailable state, without automatic retry.
The private copy is removed on exit.

Copying with before/after size/inode/mtime/ctime checks is a quiescence guard,
not a transactional snapshot protocol with a concurrent writer. Inspect stopped
runs for reproducible evidence. Metadata/artifact observations are sequential
and are not an atomic snapshot of the full filesystem. Fixture tests verify
content and write metadata remain unchanged, including a committed WAL case;
this does not promise unchanged run bytes under external concurrent activity.

Before target-following native calls, the inspector checks every existing path
component using lstat. Symlinks are refused before descending, including root
ancestors, namespace directories, state, manifests and metadata ancestry. Namespace
enumeration is limited before native projection. These checks assume quiescent
paths: native code can reopen paths, so this is not a concurrent path-swap sandbox.
Inspection path arguments and persisted output paths containing a raw `..`
component are rejected before normalization, even without symlinks. An explicit
`run_root` using that representation produces a structured input error. This
prevents lexical folding from hiding `link/..` filesystem traversal.

The chosen limits are returned in `limits`:

| Scope | Input / observation limit |
| --- | --- |
| DB + WAL copy | 64 MiB total |
| Invocation / manifest file | 32 KiB each |
| Invocation reads combined | 64 KiB per inspection |
| Invocation directory | 100 immediate entries |
| Config snapshot | 32 KiB stored JSON before loading |
| Completion snapshot | 100 input rows and 64 KiB stored cells **summed across all three native input tables**, before native loading |
| Identity table | 64 KiB file and 100 rows before native parsing |
| Native namespaces combined | 128 immediate entries across the naming reader's fixed roots |
| Epoch input tables | 128 rows and 256 KiB stored cells per inspected table |
| Native projection config | 8 KiB compact ASCII JSON; each top-level string <=1024 characters |
| Diagnostics | 24 issues, 160 characters each; further issues counted in `issues_omitted` |

The complete response is limited to **256 KiB including the CLI newline** using
`encode_inspection`: sorted keys, `ensure_ascii=True`, `allow_nan=False`, compact
comma/colon separators, ASCII bytes. CLI uses exactly that encoder. Other JSON
encodings can have different sizes. A 32 KiB envelope reserve covers bounded
issues and coverage summaries. Inputs and rows are checked before large native
materialization; bounded sections/records are admitted incrementally. The final
size guard returns a structured error if the envelope still cannot fit.
If state-budget preflight itself fails, epoch and completion materialization
remain withheld, original-root compatibility stays unknown/null, and dependent
native projection is not called. A separately bounded config read may still
succeed; its own byte check must pass. Preflight errors never restore an
unrestricted native reader or imply zero total completed records.

Completion admission is independent of epoch admission. Its aggregate budget
covers `task_coordinate_state_v1` (authority),
`task_completion_storage_state_v1` (storage policy), and `task_done_coordinate_v2`
(current DONE). All stored columns of all three tables count; the row limit
includes singleton rows, not just candidate DONE rows. Several individually
small tables cannot jointly exceed the 64 KiB or 100-row total. Missing tables
contribute no input cells, but their absence/schema/authority is still validated
by the native reader when admitted. `completion_allowed` means only that these
inputs fit the budget; it does not establish completion authority.
`state.completion_budget` exposes the scope, per-table presence/measurements and
aggregate totals. A measurement error makes the totals unknown and admission
false. An over-budget result withholds the whole completion snapshot while
keeping `records_total` unknown, even if the unvalidated DONE table has zero
candidate rows. Separately budgeted config and epoch evidence may remain usable.

These fixed defaults admit the measured chr1 development run's 63 completion
input rows / 44,930 stored-cell bytes and its 61-row / 246,030-byte reuse table.
Only completion's aggregate byte cap and epoch's per-table byte cap were raised;
row limits, the 64 MiB state-copy cap and the 256 KiB encoded-response cap remain
unchanged. There is no unlimited override. Larger inputs are still withheld,
and admitted state never clears original-root compatibility or enables combined
artifact health/scientific assessment.

Budgets omit whole config/completion/identity snapshots rather than truncate
their fields or rows. Status `omitted`, null payload, source/scope and reason
identify missing evidence. Returned record/row counts are zero for such omitted
snapshots. A completion `candidate_rows_total` counts unvalidated database rows;
it is not authoritative DONE count. Identity totals are null with a lower bound
when only the first 101 rows were examined. Invocation snapshots omitted for
input or response budgets remain unavailable for historical reconstruction;
current defaults/files are never substituted. Primary run evidence is admitted
before invocation history. These budgets do not estimate pending pipeline work.

No recursive output/log scan, BAM scan, task scheduling, original-run write,
NiceGUI service, database service or UI registry update occurs. Ordinary reads
may update access times; fixture invariance excludes atime.

## Gene diagnostics

The pure engine in [gene_diagnostics.py](gene_diagnostics.py) accepts parsed
records only. Its [v2 contract](gene_diagnostics_contract.json) defines explicit
sample/cell/gene/strand identity, raw and retained site-assigned counts (`S`),
eligible terminal counts (`T`), retained intronic counts (`I`) and a uniquely
defined distal terminal count (`D`). Different denominator recipes are returned
separately with availability reasons. Missing support is not silently converted
to a zero, and diagnostic fractions are not renamed as native iPUI.

The native-file adapter applies the same engine after checking the caller's
explicit files and their SHA-256 values against supported native schemas and
the selected peak/annotation/matrix namespace:

```bash
python -B scpolya_agent/native_gene_diagnostics.py --request request.json
```

Build `request.json` from the
[native request contract](native_gene_diagnostics_contract.json). It requires
the run root, single-sample binding, explicit namespaces, selected gene/cells,
an input table, artifact-name manifest, invocation snapshot, cell registry,
count matrix and catalogue. Native PSI and per-site/gene-total iPUI files are
optional, with at most nine source files altogether. There is no automatic
latest-run or filename selection.

For the Python API, load `native_gene_diagnostics.py` with `importlib.util` as
the contract specifies. Use the direct-script CLI above rather than `python -m
scpolya_agent.native_gene_diagnostics`: package initialization currently imports
the separate inspector. The adapter's supported loading path preserves host
logging state and does not import the inspector or access SQLite.

The default request limit is 256 gene-cell units across all selections and
1 MiB of canonical JSON output; optional overrides can only reduce limits.
Three genes across 96 cells therefore exceed the unit limit. Integrity or
budget failures return a structured refusal without partial gene results;
the CLI exits 2. Successful diagnostics exit 0. These tools do not establish
whole-run health or biological validity. Historical run commits, upstream
completeness and the link from the original input CSV to its run copy remain
explicit caller provenance rather than authenticated facts.

## Fixed-feature coverage operation v1

[coverage_diagnostics.py](coverage_diagnostics.py) exposes direct-script
`plan`, `run`, and `get`, and file-loaded Python functions `make_request`,
`plan_coverage`, `run_coverage`, `get_coverage`, and `guarded`. It imports only
standard-library modules until its fixed worker has checked the actual runtime.
Use the direct script or the file loader below; importing through the package
initializes the existing inspector.

The request supplies exactly five absolute regular-file paths: `N.npy` contains
cell-by-fixed-PAS counts and `T.npy` contains cell-by-fixed-gene totals. Both must
be C-order little-endian int64, nonnegative, with every element at most
10,000,000, and each T element must equal the corresponding N gene-block sum.
The ceiling applies to each element, including T; a global total above ten
million is valid. The three UTF-8 LF index files have these exact headers:

```text
cell-index.tsv:    cell_index barcode major_class cell_type
feature-index.tsv: feature_index terminal_gene_unit_id peak_order_in_gene peak_name
gene-index.tsv:    gene_index terminal_gene_unit_id start stop K
```

The fields shown above are separated by tabs in the files. Ordinal indices are
zero based, contiguous canonical decimal integers. Gene blocks are contiguous
half-open `[start, stop)` partitions with K>=2; native `peak_order_in_gene` is
positive and may contain gaps. Barcodes and gene units must already be unique
and canonically sorted; features follow UTF-8 gene unit, native order, then
UTF-8 peak name. Barcode order follows Python string order (the same UTF-8
order for valid Unicode). Nothing is sorted, filtered, renamed, or coerced.
Literal `001`, `NA`, Unicode IDs, fixed zero sites and zero cells are retained.
Exactly two distinct nonempty group labels must match `major_class`. Group
order and `sample_id` are explicit caller context; sample identity and native
input completion are not independently established.

Maximum dimensions are 512 cells, 64 genes and 256 PAS. Total input bytes and
total attempt output bytes are each capped at 2 MiB. Known `limits` keys can
only reduce the limits in the
[machine-readable method contract](coverage_diagnostics_contract.json).
The largest possible integer accumulation is 1,310,720,000,000, safely below
both 2^53 and int64 overflow. Header/shape/index/dtype/order bounds are checked
before numeric allocation; negative counts, count bounds and T/N agreement
are checked before accumulation. A plan checks bounded byte and index
structure without loading numeric arrays; numerical validity is checked by run.

This example generates every byte count and SHA from explicit files. Set the
paths, sample and groups for the supplied bundle; create the parent attempt
folder first. The attempt itself must not exist and must be outside every
source-file parent directory tree. Python/NumPy versions, current source,
pinned method, indices, groups, limits and attempt path are bound in the plan.
Plan creation is deterministic and read only; this example explicitly writes
the request and plan with exclusive creation.

```python
import importlib.util
from pathlib import Path

repo = Path('/absolute/path/to/checkout')
spec = importlib.util.spec_from_file_location(
    'fixed_coverage', repo / 'scpolya_agent/coverage_diagnostics.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)
source = Path('/absolute/path/to/input-directory')
request = coverage.make_request(
    {role: str(source / role) for role in coverage.ROLES},
    sample_id='declared-sample', groups=['first-group', 'second-group'],
    attempt_dir='/absolute/path/to/attempts/attempt-001')
with open('/absolute/path/to/request.json', 'xb') as out:
    out.write(coverage.wire(request))
plan = coverage.plan_coverage(request)
plan_bytes = coverage.wire(plan)
plan_path = '/absolute/path/to/plan.json'
with open(plan_path, 'xb') as out:
    out.write(plan_bytes)
plan_ref = {'path': plan_path, 'sha256': coverage.sha(plan_bytes)}
```

For the CLI, `plan --request /absolute/request.json` emits the canonical plan;
`run --plan PATH --plan-sha SHA` returns the generated `analysis_ref`; and
`get --plan PATH --plan-sha SHA --manifest PATH --manifest-sha SHA` verifies an
existing result. Pass references programmatically from these outputs; do not
hand-copy hashes into agent-generated citations. Unknown actions, keys, flags,
roles or analysis fields return a structured refusal (exit 2).

Fresh execution requires Python `-B`, all six thread variables equal to `1`,
and inherited cgroup v2 leaf controls of at most 2 CPUs, 8 GiB memory and zero
swap. These are read and verified before the numeric import. The fixed host
launcher [fixed_coverage_limited.sh](../scripts/fixed_coverage_limited.sh)
uses the designated `scpolya-remaster` Python, a user systemd unit with
`RuntimeMaxSec=115s`, `TimeoutStopSec=5s`, and `KillMode=control-group`.
It requires this host's user systemd bus. The fixed child uses the current
Python executable, a monotonic budget of at most 110 seconds inclusive of its
termination grace, and TERM followed by process-group KILL on expiry.
The child also arms a Linux `SIGALRM` default-action `ITIMER_REAL` fuse at
private worker entry, initially at 110 seconds and then shortened to the
plan's budget measured from that same entry. It explicitly unblocks SIGALRM.
This independent OS deadline precedes handoff waits and numeric imports.

Only the fixed parent passes an anonymous inherited read FD and fresh nonce.
A bounded, nonblocking, one-use pipe message binds that nonce, parent, plan and
exact start receipt; the worker closes the endpoint before claiming the attempt
or importing NumPy. Missing/invalid FDs, regular files, mismatched messages,
replayed consumed pipes and EOF refuse without a claim or payload. Both parent
writes and child reads have deadlines; a leaked writer cannot hang the child.
Disk members and a matching PID alone do not enable the old private entry.
The nonce itself is never written to disk; audit receipts record its digest.
This is an internal handoff, not cryptographic authentication against another
process running as the same user or a malicious host. The package does not control arbitrary systemd jobs or run caller commands.
Observed leaf CPU/memory/swap/thread controls are recorded; observation of a
process does not independently prove the external supervisor's wall limit.

```python
import json, subprocess
completed = subprocess.run(
    ['bash', str(repo / 'scripts/fixed_coverage_limited.sh'),
     plan_ref['path'], plan_ref['sha256']],
    capture_output=True, check=False)
run = json.loads(completed.stdout)
if run['status'] == 'complete':
    summary = coverage.get_coverage(plan_ref, run['analysis_ref'])
    detail = coverage.get_coverage(plan_ref, run['analysis_ref'], 'gene-group')
    cells = coverage.get_coverage(plan_ref, run['analysis_ref'], 'per-cell')
```

`run` refuses every existing attempt directory. It creates an exclusive start
receipt first, preserves failure/timeout/crash partials and publishes the final
manifest last with no replacement, after source-before/after, code and delivered
output verification. A publication guard remains until the final-link directory
sync succeeds. If linking succeeded but pending-file cleanup or the final sync
fails, the visible final manifest is retained and the failure receipt reports
`visible_durability_uncertain`, `complete_manifest_published=true`, the failing
stage and uncertain directory durability. The guard makes `get` refuse even
when writing the failure receipt also fails. Before-link failures report
`not_published`; visibility inspection failures report `visibility_unknown`
rather than inventing a Boolean. Normal guard removal follows confirmed
manifest directory sync; a crash restoring the guard conservatively refuses a
durable result. No final manifest is deleted to conceal a publication fault.
A killed process may leave only partial receipts rather
than a caught failure JSON. A retry requires a new directory and a new plan;
there is no deletion, automatic retry, repair or legacy adoption.

`get` requires both explicit plan and result-manifest identities. It rechecks
current source bytes, method/code and Python/NumPy versions, all delivered
schemas, member names, byte counts, hashes, TSV headers, row counts, identities
and saved-output ledger invariants. It never loads N/T numerically, calls the
calculator, launches a worker, or writes. It may sum and compare saved output
ledgers and saved summary statistics as consistency checks. Reuse mismatches
return a refusal; no automatic replanning or calculation occurs.

Summary fields have generated definition/unit references, including separate
joint intersections and group marginals. Detail views return exact JSON
pointers plus TSV hashes, actual headers and primary keys; each field resolves
to the delivered method definition. JSON pointer escaping is RFC 6901.
Fractions use 0-to-1 units; positive-only medians are null when no cell is
positive; second-largest pooled counts do not identify distal sites. Pooled
multi-site support and within-cell multi-PAS support remain separate. Numeric
TSV values use `.17g` and are reparsed and checked against the delivered JSON.

Completion reports `execution_state=completed`, `review_state=not_reviewed`,
and `native_input_completion_state=not_assessed`. `get` returns
`fresh_analysis=false`. These are bounded trusted-host consistency claims;
SHA consistency is neither owner approval nor authentication against a
coherent malicious rewrite. This slice has no inventory, global cache, adapter,
R2/null/UMAP/p-value/power calculations or scientific acceptance decision.
The independent fixture in `tests/fixtures` is explicitly hand-derived;
synthetic tests do not establish real-data parity.

The numeric method contract is unchanged by these lifecycle repairs.
`worker-claim.json` now uses schema `scpolya.fixed_coverage.worker_claim.v2`
with `handoff` and `child_fuse`; the manifest's `child_supervision` carries
matching records. Caught failures use `scpolya.fixed_coverage.failure.v2`
with explicit publication stage, visibility and directory durability.
Old plans are stale after a source change; there is no legacy adoption.

## Native-v3 fixed coverage preparation

[native_coverage.py](native_coverage.py) converts explicit native sources into
`N.npy`, `T.npy` and the three canonical coverage indexes. Its
[pinned recipe](native_coverage_recipe.json) fixes eligibility and budgets.
Use the direct script or load the file with `importlib.util`; package imports
initialize the existing inspector. The accepted coverage module, method and
launcher remain unchanged.

The sole write action is `prepare`. Six source roles are mandatory: `counts`,
`catalogue`, `config`, `manifest`, `inputs`, `cohort`; `registry` is optional.
Each descriptor is an explicit canonical absolute path, byte count and SHA. Native sources
must match the selected run/namespace paths. Config is an explicit invocation-v2
snapshot with integer config-schema1; the artifact manifest is short-name v1.
All active identity fields must be present, with exact canonical config and
pk/an/mx payload/digest agreement. The native annotation identity must be
`terminal-annotation-v3`. The native counts/catalogue filenames end in `v2`,
which does not enable historical annotation-v2 interpretation.

Counts have exact columns `peak_name`, `cell_barcode`, `umi_count`. Catalogue
requires the 15 fields named in the recipe and accepts only its nine known
optional columns (the current complete profile has 24 columns). The parser
streams gzip to EOF, validates CRC, original compressed SHA, duplicate pairs,
unknown mappings and source stability even for rows excluded from the output.
No native packages, inspection, logging setup, SQLite or research runtime are
imported. Native identity reconstruction is a narrow explicit port; a preloaded
`quantification_config` from another checkout cannot supply defaults.

Select unique true `gene_id`/`strand` units and supply a canonical four-column
cohort (`cell_index`, `barcode`, `major_class`, `cell_type`). Exactly two declared
nonempty groups must match. Every selected gene must have at least two native
eligible terminal PAS; unsupported genes refuse with no silent removal.
Eligibility means `Terminal_exon` or `Downstream_of_terminal_exon` with a finite
ratio passing the bound general threshold and consistent native pass flags.
All these sites, zero genes and zero/unobserved cells remain. No pooled-positive
filter, tuning, top counts or adequacy selection occurs. Valid all-zero input
produces `data_state=all_zero`.

Raw and retained counts are bounded at 10M per element, and each T gene/cell sum
must also stay within 10M; global totals above 10M are valid. Conversion accounting
is an exact disjoint partition in this precedence: outside cohort, unassigned
gene, unselected gene, nonterminal, general-fail terminal, retained. Legal
positive excluded counts stay in that ledger. IDs such as `001`, `NA` and Unicode
remain strings; units use injective percent encoding. Distinct PAS IDs at a
shared coordinate stay distinct. One peak name assigned ambiguously to multiple
units refuses rather than duplicating its counts.

An optional registry is a unique, headerless UTF-8 LF one-column whitelist. With
it, `registry_sample_id` is required and must equal `sample_id`; the helper below
writes that explicit declaration by default. Cohort and every source count cell,
including excluded rows, must occur in the registry. Registry order never changes
the cohort or labels. Without it, membership is declared by the cohort and global
registry validation is unassessed. Source-row absence and zero counts after
eligible selection are separate observations; registration means only membership
in the supplied declared list and does not establish BAM or biological absence.

Optional `cohort_origin` contains exactly `sample_id`, `original_selection`
(path/bytes/SHA), and `recipe_id=barcode_group_to_cell_index_utf8_v1`. That fixed
projection accepts exact UTF-8 LF `barcode,group` TSV, sorts raw barcode UTF-8
bytes globally, and emits canonical `cell_index,barcode,major_class,cell_type`
with both labels equal to the original group. Projected bytes must exactly equal
the supplied cohort. Original selection, recipe/digest, row mappings and actual
cohort are bound in the manifest and handoff. Omission means direct canonical
cohort authority, with origin/projection unassessed; it does not alter cell_type.

The library helper generates source hashes without copying values by hand:

```python
import importlib.util
from pathlib import Path

repo = Path('/absolute/path/to/checkout')
spec = importlib.util.spec_from_file_location(
    'native_fixed', repo / 'scpolya_agent/native_coverage.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
paths = {
    'counts': '/absolute/native/count_matrix/pk-.../an-.../sample.directional.annotated.counts.terminal-aware.v2.tsv.gz',
    'catalogue': '/absolute/native/matrix_summary/pk-.../an-.../mx-.../an-....terminal_aware_peak_to_gene.v2.tsv.gz',
    'config': '/absolute/native/logs/invocations/explicit-id/invocation.json',
    'manifest': '/absolute/native/state/artifact_name_manifest.v1.json',
    'inputs': '/absolute/native/input_tables/inputs.csv',
    'cohort': '/absolute/external/cohort.tsv',
    # 'registry': '/absolute/external/registry.txt',
}
request = adapter.make_request(
    paths, run_root='/absolute/native', sample_id='sample',
    namespace={'peak': 'pk-...', 'annotation': 'an-...', 'matrix': 'mx-...'},
    artifact_base='sample.directional.annotated',
    invocation_inputs_path='/original/inputs.csv',
    sample_source_path='/declared/input.bam',
    groups=['first', 'second'],
    selected_units=[{'gene_id': 'explicit-gene-id', 'strand': '+'}],
    attempt_dir='/absolute/new-attempts/prepared-001')
# Optional exact original-cohort projection, with generated descriptor:
# request['cohort_origin'] = adapter.make_cohort_origin(
#     '/absolute/external/original-selection.tsv', 'sample')
with open('/absolute/requests/prepare.json', 'xb') as out:
    out.write(adapter.wire(request))
```

Run the fixed deployment launcher (requires this host's designated Python and
user systemd bus):

```bash
bash scripts/native_coverage_limited.sh /absolute/requests/prepare.json
```

It runs one dedicated `prepare --request PATH` process with actual cgroup-v2 leaf
controls at most 2 CPU, 8 GiB RAM and zero swap, six numeric thread variables1,
Python-B, and systemd115s plus stop5s. Preparation independently arms Linux
SIGALRM default action via ITIMER_REAL at entry, at most110s. Library use requires
the main thread and an idle real timer: an existing timer is refused without
changing it. On success/caught refusal the adapter disarms only its own timer,
consumes any pending own alarm and restores the prior handler/mask. Do not
concurrently manipulate process alarm state; dedicated CLI execution is preferred.
Read-only import/bridge/link/verify never arm timers. In-process observation of
leaf controls does not independently attest systemd's external wall deadline.

Limits include512 cells/64 genes/256 PAS;32MiB per source/64MiB total;1M count
rows/20K catalogue rows/10K source or registry cells;256MiB expanded and192MiB
source read IO including rehash. Metadata is at most1MiB each. Selected context
is at most2K rows; the full eligibility/lineage ledger is at most1MiB. Five files
are at most2MiB combined and the full attempt at most4MiB. Known integer overrides
can only reduce limits. Exact caps/headers are in the recipe. Conservative
refusal near combined limits is possible; no automatic batching or truncation.

Fresh directories are exclusive. Start/source/code/request snapshots precede
conversion, actual staged bytes are reread, and final manifest is published last
without replacement after source/code checks. A publication guard persists on
visible-but-durability-uncertain failure, including partial link/unlink faults or
failure-receipt write failure. Final manifests are never deleted to conceal
faults. Failed/crash partials remain; retries need new explicit directories.

The preparation response contains generated `prepared_ref`. Carry native origin
alongside ordinary coverage using the read-only bridge and sidecars:

```python
# prepared = json.loads(the_launcher_stdout)['prepared_ref']
bridge = adapter.coverage_request_from_prepared(
    prepared, '/absolute/new-attempts/coverage-001')
coverage = adapter.c  # fixed pinned adjacent accepted coverage helper
plan_bytes = coverage.wire(coverage.plan_coverage(bridge['coverage_request']))
plan_path = '/absolute/handoff/coverage-plan.json'
with open(plan_path, 'xb') as out:
    out.write(plan_bytes)
plan_ref = {'path': plan_path, 'sha256': coverage.sha(plan_bytes)}
planned = adapter.link_coverage(prepared, plan_ref)
with open('/absolute/handoff/planned.json', 'xb') as out:
    out.write(adapter.wire(planned))
# Execute the existing fixed_coverage_limited.sh with plan_ref, parse its run JSON.
# completed = adapter.link_coverage(prepared, plan_ref, run['analysis_ref'])
# Persist completed to a NEW exclusive sidecar; never overwrite planned.json.
```

Read-only CLI verbs are `bridge --prepared PATH --prepared-sha SHA --attempt-dir
PATH`, `link --prepared PATH --prepared-sha SHA --plan PATH --plan-sha SHA`
(optionally `--result PATH --result-sha SHA`), and `verify --envelope PATH
--envelope-sha SHA`. Persist generated envelopes exclusively outside native,
prepared and coverage attempt trees. `verify_handoff` validates the generated
schema, source/shape/selection/producer/recipe, plan/result identities and actual
JSON-pointer/header/primary-key references. No module/plugin dispatch, discovery,
repair, numerical preparation or coverage calculation occurs. Plain coverage
`get` alone does not verify native origin.

Every read view reports `native_freshness=checked_at_preparation_only` and
`selection_freshness=checked_at_preparation_only`. Current prepared bytes and
producer/recipe are checked; original native/selection sources are not rehashed
or recounted by the bridge. Native config/sample/path/namespace consistency is
observed. External cohort/registry sample origin, biological labels, original CSV
copy provenance, quiescence, scope completeness and historical commit remain
caller declarations. Execution completed, scientific review not_reviewed and
native completion not_assessed remain separate; SHA consistency is not approval
or hostile-host authentication.

Read-only handoff validation also checks internal scientific consistency. The
prepared auxiliary `native-config.json` stores the exact original invocation
bytes (within the metadata and whole-attempt caps), bound to the request's
config descriptor. The reader reconstructs the active scientific payloads and
nested pk/an/mx identities from this snapshot, and requires the saved binding
to match the request's sample, root and artifact base exactly. It revalidates
saved catalogue context fields against these thresholds, checks a bijection
between eligible contexts and output features with complete K/order, and
compares every provenance field against the request and fixed semantics. These
checks use saved bytes only; original-source freshness remains
`checked_at_preparation_only`. They check consistency, not authenticity of a
whole history rewritten by the same filesystem user.

## Pre-dedup tail profile v1

`scpolya_agent/tail_profile.py` profiles one aligned BAM read-only, before UMI
deduplication, with the exact per-record tail rule of
`utils.polya_extract.extract_polya_bam` (the native module is neither imported
nor modified; tests compare against it on synthetic BAMs).

```bash
python -B -m scpolya_agent.tail_profile --request req.json --output new.json
```

```json
{"schema": "scpolya.agent.tail_profile_request.v1", "bam": "/explicit/in.bam",
 "contigs": ["chr2"], "minpolyA_values": [5, 10], "nonA_allowed": 1, "minMAPQ": 10,
 "max_records": 5000000, "sampling": {"policy": "head"},
 "expected_sha256": {"bam": "<64 hex>", "index": "<64 hex>"}}
```

* Units are alignment records, not molecules. Results are not expected to
  equal the native extractor's counters, which count post-dedup records.
* `max_records` caps records examined (not bytes read: `hash_source` hashes the
  whole BAM); `truncated_at_cap` says whether more existed. `head` is the first
  records in scope order (not random). `qname_hash` (`fraction`, `seed`)
  decides per query name without a read-group/sample namespace, only among
  records examined before the cap.
* `waterfall.totals` (examined, sampled) are totals; `waterfall.partition`
  (not_sampled, read2_flag, unmapped, missing cell tag, missing UMI tag,
  MAPQ < q, tail stage) is an ordered disjoint partition of examined records.
  The first four gates mirror umi_tools 1.1.6 dedup without `--paired`; MAPQ
  mirrors the extractor. Dedup grouping is not mirrored. Secondary,
  supplementary, duplicate/QC-fail flags, tail-end hard clips and missing SEQ
  are overlapping labels, never summed.
* Every `minpolyA` value is evaluated on the same records with the same k/q.
  `m` requires both tail-clip length >= m and A (T on reverse) count over the
  whole clip >= m; `k` bounds non-A bases anywhere in the clip. Classes:
  `retained`, `short_clip`, `low_A`, `too_many_nonA`; `native_counter_mapping`
  maps them to the extractor's counter names. `joint_clip_A` holds exact
  (clip length, A/T count) pairs, so any (m, k) can be recomputed; it is null
  when more than 65,536 distinct pairs occur.
* Record sets: `upstream_gated_pre_dedup` (no explicit secondary/supplementary
  filter upstream, though dedup may collapse such records) and `primary_only`.
* SEQ `*` records are excluded from tail classes; `native_would_raise_records`
  counts reverse records on which the native extractor would raise.
* The BAM (stat; sha256 with `hash_source` or an expected hash), the opened
  index and the whitelist (stat + sha256) are identified before the scan and
  rechecked after; any change or `expected_sha256` mismatch is refused. Code,
  git and Python/pysam/htslib versions and attempt timing are recorded. No
  recommendation or biological interpretation is produced.

## Native bridge

From a complete source checkout, the agent entry exposes the native bridge:

```bash
python -B -m scpolya_agent native-entry --help
python -B -m scpolya_agent native-entry --source-root /complete/checkout settings --preset manuscript-v4
python -B -m scpolya_agent native-entry --source-root /complete/checkout driver -- status --plan /existing/plan.json
```

This uses a fresh isolated subprocess and the unchanged accepted native admission
checks; copied package files alone are not a native runtime. Plans are
not created by the bridge. Four native instruction skills are provided under `skills/`.
See `docs/native-product-entry.md` for source/interpreter/plan binding and action
semantics.
