# Optional DeepCPS annotation

The native pipeline can annotate the resolved annotated peak BED after `annotate_peaks` and before BAM/count stages. DeepCPS is **off by default**. `--no-deepcps` explicitly disables it. Disabled runs do not import or require torch through this integration. NumPy/pandas/pysam/pyBigWig are loaded only inside the enabled implementation; existing native stages retain their existing dependencies.

Six weight files and three calibration/operating-point resources are pinned by SHA256 in `modules/deepcps/supplied_candidate_manifest.json`. The weights and calibration tables are included in the repository under `models/deepcps/` (MIT License). Point `--deepcps-model-dir` to that directory or to a read-only copy of it.

## Invocation

Add these flags to an otherwise valid native `run` command:

```bash
--deepcps \
--reference-fasta /references/alignment-genome.fa \
--deepcps-model-dir models/deepcps \
--deepcps-threads 2 \
--deepcps-batch-size 128
```

`--deepcps-model-manifest` accepts an explicit manifest with the same schema and all nine required resource hashes. Omitted, it uses the bundled resource pins. A changed resource that does not match its manifest is refused. A deliberately changed model requires a corresponding explicit manifest and receives a different task/output identity.

`--reference-fasta` must be the BAM alignment reference, indexed with an existing `.fai` (and `.gzi` for BGZF where applicable). The stage never indexes or modifies a source reference. Reference names and lengths must exactly agree with the cohort BAM `@SQ` dictionary and the run's filtered standard chromosome sizes. BAM `M5` digests are checked against uppercase FASTA sequence wherever present. No chromosome aliases are guessed. An optional `--deepcps-reference-sha256` verifies a user-supplied alignment-reference byte pin. When BAM M5 is absent, concordant names/lengths and a recorded FASTA hash cannot independently prove that the BAM was aligned to those exact bases; provenance explicitly records that limit.

Inference runs on CPU, with 1–2 threads and batch size 1–512. Install optional inference dependencies into a dedicated environment containing the normal native pipeline dependencies; the validation environment used Python 3.11.14, torch 2.14.1+cpu, NumPy 2.4.6, pandas 3.0.6, pysam 0.24.1 and pyBigWig 0.3.26. No global environment is changed. A possible task-environment installation is:

```bash
uv venv --python 3.11 /task/deepcps-env
uv pip install --python /task/deepcps-env/bin/python torch --index-url https://download.pytorch.org/whl/cpu
uv pip install --python /task/deepcps-env/bin/python numpy pandas pysam pyBigWig
```

The main native pipeline's other dependencies are still required when using its CLI. The agent-query wheel's dependency list is separate and is not expanded to require torch.

## Coverage and windows

Coverage paths use `RunLayout`, `ScItem`, the configured dedup method, expression coverage scaling and the cached UPM factor. `coverage_scaling=none/polyA` selects unscaled expression coverage; `expr/both` selects scaled expression coverage. Tracks are cohort **same-strand total-read** coverage, never poly(A)-only, removeCDS, or negated display coverage. These are the same path constructors used by `call_peaks`; there is no directional/scaled path constant.

The called base is 1-based `BED end` on `+` and `BED start + 1` on `-`. The 201-base window spans ±100 bases, with the called base at index 100. Chromosome boundaries are padded with N and zero coverage. Minus windows are reverse-complemented and their coverage reversed into transcript orientation. Scoring uses one-hot plus per-window max-normalised coverage; localisation uses only one-hot sequence. The architecture is adapted directly from the supplied handoff, with strict state-dict loading and `torch.load(weights_only=True)`.

Every native BED row is retained. Mitochondrial sites remain present with `mitochondrial_outside_training_scope`. Missing FASTA/coverage contigs, missing coverage files, all-zero coverage windows and all-unknown sequence windows produce NA scores plus an explicit reason. Invalid BED intervals/strands, mismatched reference lengths, non-finite non-NaN coverage, or negative coverage are refused. Ordinary sparse bigWig NaNs inside otherwise valid coverage become zeros. A missing strand bigWig is retained as `coverage_file_missing` NA rows rather
than refusing the whole stage; inspect `n_scored` and the reason histogram in
`provenance.json` instead of treating stage completion as scoring every site.
A predicted localisation outside the reference has `locate_site=NA` and `locate_reason=prediction_outside_reference`, while its valid score remains available.

## Outputs and identity

The original 15/16-column annotated BED is read-only. Outputs are under its parent:

```text
deepcps/<full-request-sha256>/
  scored.tsv
  sites.deepcps.tsv
  provenance.json
```

`scored.tsv` contains one row per native BED row, a stable key, name/chromosome/start/end/strand/called site, three seed probabilities, their raw mean, isotonic calibration, localisation offset/site/confidence, and score/localisation reasons. `sites.deepcps.tsv` is the user-facing joined site table: all original BED fields followed by the stable key, original row number, called base and DeepCPS fields. Numeric scores use Python's round-trip float representation. The join verifies the full key set and name/coordinates/strand, rejects duplicates/mismatches, and preserves original BED row order.

A stable site key is SHA256 of `[name, chrom, integer_start, integer_end, strand]`. Neither this key nor the joined table replaces the native peak identifier used by counts.

The request binds actual BED, FASTA/index, coverage, filtered chromosome sizes, all six weights, calibration/threshold resources, model manifest, inference settings, dependency versions and Python implementation bytes. The BAM reference dictionary is hashed; the BAM alignment body is not read or hashed because only its header is used by this annotation. Inputs are hashed again before publishing newly computed scores. A completed cache is reused only if the request and both data-file hashes match. Same-path byte changes do not silently reuse old results. Source/model identity and policy are recorded in `provenance.json`. Absolute input
paths, thread count and batch size also enter this identity: relocation or changed
inference settings recompute conservatively even when file bytes match. Every
enabled invocation hashes the full alignment FASTA and checks available BAM M5
digests before cache lookup; this has a reference-size-dependent cost.

The `deepcps:score_and_join` inline task records content identity in the native SQLite completion-observation ledger on each successful stage call. Log messages and the returned result distinguish `executed` inference from `reused` content-verified outputs. The inline event records successful completion of that call; it does not itself assert that model inference was fresh. The content-verified provenance/data files, rather than a legacy DONE flag or file existence, govern inference reuse. Output projection includes the optional DeepCPS files only when enabled.

**Scores annotate.** No default filtering occurs, UMI counts are never multiplied by scores, and localisation never changes a counting interval. Any future filtering/relocation workflow requires an explicit scientific policy and fresh quantification.

## bedGraph-to-bigWig safety repair

`utils.io_utils.convert_bedgraph_to_bigwig` validates four-column rows, known chromosomes, finite numeric values and half-open bounds; externally sorts a temporary copy with `LC_ALL=C`, chromosome/start/end keys and a 64 MiB sort buffer; and rejects overlaps. It calls the real UCSC converter on that sorted temporary file, checks a nonempty result, then atomically publishes it. Failed conversions remove partial temporary outputs and do not leave an older destination that appears current. The bedGraph itself is never rewritten; peak calling and UMI counts keep their existing input bytes. Signed negated display tracks remain supported by this general converter; DeepCPS deliberately selects non-negated total-read tracks.

Only the `generate_bigwig` task gains the `validated-sorted-bedgraph-v1` schema parameter, so old unvalidated bigWigs are not accepted under the new task identity. Peak/call/count task parameter keys are otherwise unchanged. Resuming a pre-upgrade
run regenerates its bigWigs under the new schema identity, but the rerun preview
does **not** report this schema upgrade because it is an item property rather
than a configuration key; preview output must not be described as warning about it. The audited call chain is `main -> call_peaks -> generate_bigwig_worker -> convert_bedgraph_to_bigwig -> sort -> bedGraphToBigWig`; both scaled/unscaled and total/polyA/removeCDS/display variants go through this converter.

## Validation and limits

`tests/test_deepcps_integration.py` covers positive/negative orientation, boundary padding, missing/zero/invalid coverage, reference length/M5/pin errors, explicit disable/lazy imports, native scaled/unscaled/dedup path resolution, 15/16-column compatibility, safe joins, unchanged count intervals, same-path input/model changes, corrupted output caches, mid-inference input mutation, registry agreement and persisted task completion. `tests/test_bigwig_conversion.py` uses the real UCSC converter and pyBigWig readback for deliberately unsorted chr2/chr10/chr22 coverage, plus failure/invalid-input cleanup controls.

Evidence runs must verify that the real `bedGraphToBigWig` executable is on PATH
before invoking its test; an absent-binary skip is not conversion/readback PASS.

The native agent request/command bridge now materializes all seven DeepCPS
configuration fields, binds resources before run/recover admission, and exposes
a bounded `deepcps-result` reader. See [native DeepCPS agent contract](native_deepcps_agent.md).
The driver remains tied to an explicit full native checkout and an exact,
hash-verified plan.

`scripts/deepcps/validate_supplied_fixture.py --handoff HANDOFF --output NEW_OUTPUT_DIR` reproduces the supplied 1,000-window inference fixture, reconstructs those exact model inputs as a native BED, engineered FASTA and positive/negative bigWigs, verifies extracted sequence/coverage equality, runs the native stage and then checks verified reuse and its SQLite observations. The reconstruction is an engineered input-path test, not a new biological dataset or independent accuracy benchmark. Only the 300 rows with supplied localisation expectations are compared for offset/confidence parity.

No full unrelated pipeline was run. No independent new genomic BAM-to-DeepCPS cohort evaluation, model retraining, GPU validation, filtering policy, GUI integration, or formal model-identity resolution is claimed.
