# Donor-level differential APA

`scpolya-diff-apa prepare/run` is an explicit, standalone interface, also available as
`python -m scpolya_diff`. It does not run BAM processing or modify GUI outputs.
It analyzes one cell type and one specified two-condition contrast at a time.
Its analysis unit is **gene_id + strand**, encoded using the native reversible
percent-escaped identifier. This does not resolve alternative terminal exons.
Differential iPUI is not implemented by this interface.

`scpolya-diff-apa --version` reports the scPolyA release version (2.1.0).

## Installation and environment

Python >=3.10; preparation uses only the standard library. Install from the
repository with `python -m pip install .`; this adds `scpolya-diff-apa` alongside
the existing agent/operator commands. `python -m scpolya_diff --help` works from
a source checkout without installing BAM pipeline dependencies.
Machine-readable external runtime declarations are in
`requirements/differential-apa-runtime.json`.

R is required for `run`, including its package-free descriptive mode. R is called
with `--vanilla`, explicit package-library selection and single-thread BLAS/OpenMP
environment variables. User startup profiles and ambient `R_LIBS` are excluded.
Supply `--rscript /absolute/path/to/Rscript --r-library /task/specific/Rlibrary`.
No dependency is installed automatically at execution time. Missing requested
packages cause failure, rather than silently removing an endpoint.

The implementation was tested on R 4.5.2 / Bioconductor 3.22 with edgeR 4.8.2,
limma 3.66.0, statmod 1.5.2, DRIMSeq 1.38.0 and BiocParallel 1.44.0. `edger`
requires edgeR >=4.6 with `diffSplice.DGEGLM`; there is no automatic switch to
legacy `diffSpliceDGE`. Version records in each result describe the actual
execution, not a promise of statistical equivalence across package versions.
The task's environment and source-document hashes are recorded in the delivery
report. Example installation in a dedicated library (use a matching Bioconductor
release for your R version):

```r
lib <- "/task/specific/Rlibrary"
dir.create(lib, recursive=TRUE, showWarnings=FALSE)
.libPaths(c(lib, .libPaths()))
install.packages("BiocManager", lib=lib, repos="https://cloud.r-project.org")
BiocManager::install(version="3.22", lib=lib, ask=FALSE, update=FALSE)
BiocManager::install(c("edgeR", "limma", "statmod", "DRIMSeq", "BiocParallel"),
                     lib=lib, ask=FALSE, update=FALSE, Ncpus=2)
```

Record installed versions and validate namespace loading in the same vanilla R
configuration used for analysis. Installation can report a dependency failure
while other packages finish. The presence of an installed directory is not
sufficient evidence that its namespace loads.

## Inputs and preparation

`manifest.tsv` has one input library per row, with mandatory columns:

```tsv
sample_id	counts_path	annotation_path
S1	S1.counts.tsv.gz	common.peak_to_gene.tsv.gz
S2	S2.counts.tsv.gz	common.peak_to_gene.tsv.gz
```

Paths are resolved relative to the manifest. Each `annotation_path` must describe
the catalogue actually used to quantify that count file. It may point to the same
common catalogue. Separate annotations are compared using canonical fingerprints
of every row's PAS ID, genomic interval, strand, gene ID, context, transcript,
stop-codon anchor, length, signed distance and quality flag. Differences fail even
if `peak_name` strings match. Row ordering and numeric spelling do not affect the
fingerprint. Display gene names do not define identity. **This validates supplied
catalogue consistency; a table cannot prove that its declared catalogue was used
upstream.** Independently called PAS must be brought into one catalogue and
requantified before this analysis.

The common `--annotation` is a native PAS annotation TSV or gzipped TSV with:

```text
peak_name gene_id chrom start end strand feature canonical_transcript
stop_codon_position three_prime_utr_length distance_to_stop_codon
mispriming_pass general_mispriming_pass is_distal_peak
```

`gene_name` is optional. Intervals are nonnegative half-open start/end; strand is
`+` or `-`. Selected PAS require explicit gene ID; contradictory known chromosome,
canonical-transcript or stop-codon anchors within a gene-strand unit fail. Missing
anchors retain composition counts and mask the affected unit's length endpoint;
the module does not silently construct terminal isoforms. `mispriming_pass` and
`general_mispriming_pass` are explicit booleans. The native terminal set uses the
**general** flag, matching the native pipeline. `is_distal_peak` freezes native
distal membership; when signed distances are known, all maximum-distance ties
must agree with these flags. Input quality
thresholds remain the upstream analyst's responsibility and are bound through
annotation bytes/fingerprints; the module does not recalculate mispriming.

Counts are raw, nonnegative, integer molecule assignments in either headerless
`peak_name, cell_barcode, umi_count` TSV order or a headered long table.
Integer-valued decimal strings are accepted losslessly. Counts above R's integer
limit, fractions, negatives and nonfinite numbers fail. Native PSI tables with
`peak_umi_count` may be read: raw counts are authoritative and PSI/gene totals
are ignored. A PSI table may already omit unobserved or ineligible PAS; prefer
the complete raw counts source and document any upstream selection. Normalized,
imputed or log-transformed counts are unsuitable. Unknown catalogue PAS fail,
including in rows outside the selected metadata population.

Metadata has one cell per exact `(sample_id, cell_barcode)` key:

```tsv
sample_id	cell_barcode	pseudobulk_id	donor_id	condition	cell_type	batch
S1	AAAC-1	D1_CTRL_CD8	D1	CTRL	CD8T	B1
S2	AAAC-1	D2_CASE_CD8	D2	CASE	CD8T	B1
```

Donor/patient is the independent biological replicate. Pseudobulk is
**donor × condition × cell type**. Multiple disjoint technical libraries may map
to the same pseudobulk; all biological labels and batch must agree. Two
pseudobulk IDs for the same biological tuple fail. Do not split cells from one
donor into artificial replicates. The module validates label consistency but
cannot independently verify donor identity or disjointness of molecules across
libraries. Library/sample IDs must identify the correct barcode namespace;
barcode suffixes and identifier spelling are preserved.

Manifest samples must equal the samples with selected cell-type metadata.
Repeated metadata keys, repeated paths, byte-identical count files under
separate labels, duplicate selected PAS/cell rows and nonempty output directories
are rejected. Duplicate rows are never summed. The selected-row duplicate audit
uses a temporary on-disk SQLite key table, bounded by a small page cache.

```bash
scpolya-diff-apa prepare \
  --manifest manifest.tsv --metadata metadata.tsv --annotation common.tsv.gz \
  --cell-type CD8T --analysis-set native-terminal --min-cells 20 \
  --outdir results/CD8T-prepared
```

`--analysis-set` defaults to **native-terminal**; terminal-only is an explicit sensitivity choice:

| Set | PAS contexts after upstream general-quality flag | Length handling |
| --- | --- | --- |
| `native-terminal` | Terminal_exon + Downstream_of_terminal_exon | Zero/unknown lengths retained for composition |
| `terminal-only` | Terminal_exon | Zero/unknown lengths retained for composition |

These sets differ in their count denominators. The terminal-only mode is not
native terminal-aware PSI. Neither mode applies a positive-length-only
common filter. Downstream sites retain the upstream
annotation's interpretation; the module does not independently establish an
extended 3′UTR. All eligible catalogue PAS are emitted, including all-zero PAS.

By default any unmatched selected metadata cell in a surviving eligible pseudobulk fails preparation. A missing
count record can mean zero coverage or a label mismatch; it is not certified as
zero. `--allow-unmatched-cells` explicitly excludes these cells, saves their
identities and records matched/metadata counts. It does not turn unmatched cells
into measured zeroes. Pseudobulks below `--min-cells` matched cells or with no target-positive cells
are excluded and written to `excluded_pseudobulks.tsv`; eligible pseudobulks
continue. Unmatched cells in excluded pseudobulks do not abort the remaining
analysis. Matched/metadata and target-positive/metadata fractions are reported.
If none survive, preparation preserves empty sample/count columns for an
explicit empty descriptive result rather than inventing replicate data. Within matched, catalogue-qualified cells,
omitted PAS rows are treated as zero. No cell registry/BAM membership audit is
performed by this command.

Preparation writes analysis-set and full-native counts/annotations
(`pas_*.tsv` and `native_pas_*.tsv`), sample/coverage diagnostics,
`catalogue_selection.tsv`, `unmatched_cells.tsv`, `excluded_pseudobulks.tsv`, and `preparation.json` with
input/source hashes, configuration, catalogue fingerprint and output hashes.
`run` refuses failed preparations or modified intermediate files. Failures are
preserved in their output directory; retry with a new directory.

## Endpoints and design

```bash
scpolya-diff-apa run \
  --input results/CD8T-prepared --outdir results/CD8T-CASE-minus-CTRL \
  --control CTRL --case CASE \
  --rscript /usr/bin/Rscript --r-library /task/specific/Rlibrary
```

Default `--methods edger,limma` runs the primary composition endpoint and a
separate length endpoint. The primary composition algorithm is:
`DGEList` → TMM normalization → `glmQLFit(robust=TRUE, legacy=FALSE)` →
`limma::diffSplice` dispatching to edgeR's `diffSplice.DGEGLM` →
`topSplice(test="F")`. Unit p-values receive BH across **all retained/tested
units** in this one celltype/contrast. PAS outputs provide descriptive effects;
this interface does not silently introduce a site-level hypothesis family.

Select `--methods edger` for composition alone or `--methods limma` for the
length endpoint alone; these choices are recorded. It tests log2 of each donor's pooled-molecule weighted
length using `lmFit` and robust `eBayes`. Length is measured on the same retained
PAS denominator as composition. Lengths follow native `max(distance_to_stop_codon, 0)` clipping. Supplied finite
lengths must agree with that definition (negative input lengths clip to zero);
missing lengths/distances or required anchors are not silently repaired. If any
retained PAS has unavailable length/anchor, the entire unit's length endpoint
is unavailable. If any selected
pseudobulk has weighted length <=0, its log-length test is unavailable. Zero
length PAS remain in the composition counts; no pseudolength is added and no
inconvenient PAS is silently removed from its unit. Constant log-length range <=1e-10, near-exact fits
(residual sigma <=1e-8), and insufficient units for empirical-Bayes moderation
are separately marked, with NA p-values/FDR. BH uses the estimable length family.
The metric retains the upstream length definition (often genomic distance from
a stop codon); it is not automatically mature spliced-transcript length.

`--methods edger,drimseq` additionally runs a unit-level Dirichlet-multinomial
sensitivity endpoint. `dmPrecision(prec_subset=1)`, `dmFit(bb_model=FALSE)` and
`dmTest` use serial BiocParallel. No feature-level beta-binomial hypothesis family
is added. Precision, fitted proportions, full/null likelihoods, raw tests and
warnings are retained. Nonfinite/invalid precision, proportions, likelihoods or
p-values fail execution. The package exposes no general convergence flag;
finite outputs do not prove optimization convergence. Warning-bearing runs
are labelled `COMPLETE_WITH_WARNINGS` and require inspection. **Do not take the
union of significance calls across methods as FDR-controlled findings.**

Inference requires at least two independent donors per group (warns below
three), or two complete pairs for `--paired`. Below this threshold the run
automatically becomes `DESCRIPTIVE_ONLY` with reason `insufficient_replicates`,
without loading inferential packages or generating p-values/FDR. Unpaired donors
cannot recur across conditions, including in explicitly descriptive mode.
`--paired` excludes donors missing either side, writes `excluded_donors.tsv`,
then checks the surviving complete-pair count. Its inferential design is donor +
group. No surviving pairs produces an explicitly empty descriptive result. `--batch` adds the batch factor; missing batch levels,
rank deficiency and absent residual degrees of freedom fail. A group effect
confounded with recorded complete batch/donor metadata fails even if `--batch`
was omitted, except when complete batch labels are unique to every pseudobulk
and `--batch` was omitted. Such saturated unused labels cannot support batch
adjustment or a confounding assessment; inference continues unadjusted for batch
with an explicit warning. Explicit `--batch` still fails for this saturated design,
and non-saturated condition-confounded batch still fails even when omitted.
Other nonabsorbed batch variation omitted from the design emits a warning.
Missing or inaccurate metadata cannot reveal unrecorded confounding.
No automatic covariate selection or all-contrast loop occurs.

`run --descriptive-only` skips all inference and package requirements, allowing
one donor with both observed conditions. Requested condition labels must exist in metadata, including explicitly excluded
pseudobulks; an unknown label fails rather than creating a fabricated group.
If quality exclusion removes an entire known group, the resulting contrast is
descriptive with unavailable effects for that group. A single observed condition
can be prepared and inspected without inventing a contrast. Repeated donors
across observed groups still require `--paired` even in descriptive mode. Descriptive output has no p-values/FDR. Batch/paired model
adjustments do not turn descriptive effects into adjusted or population effects.

## Filtering, effects and audit outputs

PAS filtering retains a PAS when count >= `--min-pas-count` (default 5) in at
least `--min-pas-samples` pseudobulks (default smaller selected group size).
The support need not occur in both groups. Then a unit needs at least two
retained PAS and retained total >= `--min-unit-count` (default 20) in **every**
selected pseudobulk. These are engineering defaults, not calibrated universal
biological thresholds. Declare changes before examining contrast significance.

`analysis_set_unit_counts.tsv` holds the full prefilter denominator;
`retained_unit_counts.tsv`, `pas_counts_used.tsv` and `pas_annotation_used.tsv`
hold the denominator actually analyzed. `filtering.tsv`, `pas_filtering.tsv`
and `unit_filtering.tsv` enumerate before/after PAS and unit counts/reasons.
All proportions and weighted lengths are recalculated on retained PAS. No
prefilter native denominator is silently reused. Distal PAS identity is frozen from the **full native terminal catalogue**, before
analysis-set or count filtering, and aggregates **all** `is_distal_peak` ties.
If any native distal member is absent from the retained analysis set, the
analysis distal effect is **NA**, status `distal_filtered`; no nearer PAS is
relabelled distal. `analysis_*distal*` columns use retained-PAS denominators.
Parallel `native_*distal*` columns and `native_distal_effects.tsv` use the
prefilter full-native denominator. `native_unit_counts.tsv`,
`native_distal_usage.tsv` and `analysis_distal_usage.tsv` make both formulas
recomputable. Units with fewer than two native PAS have no native PSI effect;
unknown native distal identity is explicitly unavailable. For an otherwise eligible
native unit, any selected pseudobulk with zero native total gives
`native_distal_status=zero_native_denominator`. Only those undefined usage cells
are NA: other pseudobulks retain their values and all native counts remain
available. A group mean containing an undefined usage, and hence its delta,
remains NA. Low-PAS and unavailable-distal-identity statuses retain priority.
Filtering can make the
two denominator-specific descriptive effects differ; this is not an additional
biological discovery.

Effects are means of donor-level pseudobulk proportions/lengths, with
**case minus control**; PAS/distal deltas are in percentage points and weighted
length deltas are in bp. They are unadjusted descriptive effects alongside
design-adjusted tests, not fitted batch-adjusted coefficients. Both effect
provenance and contrast direction are recorded in `run.json`.

`run.json` records actual argv, module/source/input/Rscript hashes, selected
endpoints, hypothesis family, status, timestamps and output hashes. `versions.tsv`,
`sessionInfo.txt`, `design.tsv`, logs and `warnings.tsv` expose actual execution.
Completion is `COMPLETE`, `COMPLETE_WITH_WARNINGS`,
`COMPLETE_WITH_UNTESTABLE_LENGTH_UNITS`, `DESCRIPTIVE_ONLY`, or `FAILED`.
Untestable length units remain recorded if warnings are also present. There is
no unconditional `SUCCESS` sentinel. Numeric TSV values use 17 significant digits for round-trip readback. A warning status is execution evidence,
not a declaration that the science or optimizer was independently validated.

Run independent celltype/contrast jobs only under a predeclared multiple-testing
plan. This command does not adjust across separately run celltypes or contrasts,
or change any existing manuscript figure's original statistical method.

## Verification and boundaries

`tests/test_differential_apa.py` exercises catalogue mismatches, sample/barcode
isolation, exact raw count sums, duplicate/fractional input refusals, analysis-set
and zero/unknown-length handling, BH rederivation, complete paired designs,
recorded batch confounding, single-donor descriptive behavior, a symmetric null,
strong synthetic switches and separately requested endpoints. Set the dedicated
library and run under the resource limit appropriate for the host:

```bash
SCPOLYA_DIFF_R_LIBRARY=/task/specific/Rlibrary \
  python -m pytest tests/test_differential_apa.py -q
```

These are synthetic integration checks, not a broad type-I-error/FDR calibration.
Suitable raw
multi-donor PAS counts with trustworthy donor/condition metadata remain required
for real inferential validation. Do not reconstruct counts from patient/slice
proportions or treat slices/technical libraries as independent donors.

Official API references used for the tested environment:
[edgeR 3.22 manual](https://bioconductor.org/packages/3.22/bioc/manuals/edgeR/man/edgeR.pdf),
[limma 3.22 manual](https://bioconductor.org/packages/3.22/bioc/manuals/limma/man/limma.pdf),
[DRIMSeq 3.22 manual](https://bioconductor.org/packages/3.22/bioc/manuals/DRIMSeq/man/DRIMSeq.pdf).
These release URLs may later serve revised manuals.
