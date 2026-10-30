# scPolyA

scPolyA is a toolkit for identifying and quantifying poly(A) sites (PAS) and
alternative polyadenylation (APA) in single-cell RNA-seq data. It starts from
aligned 3'-tagged single-cell BAM files (for example 10x Genomics 3' libraries)
and produces annotated PAS sets, browser tracks and, optionally, per-sample
PAS×cell UMI count matrices and APA summary tables (see [Outputs](#outputs)).
Aggregation by cell type happens only in the separate differential APA tool.

The repository contains:

- **Pipeline** (`main.py`): BAM registration, UMI deduplication, poly(A) read
  extraction, peak calling, annotation against gene models and optional
  bulk-derived 3'UTR windows, and optional per-sample counting and summary
  tables. Completed tasks are recorded in a resumable state database.
- **GUI** (`scpolya_ui`): a local web interface for workspaces, run setup,
  run inspection, logs, comparisons and IGV-based track review.
- **Differential APA** (`scpolya_diff`): donor-level differential APA between
  two conditions within one cell type.
- **Agent tools** (`scpolya_agent`, `skills/`): JSON interfaces for native
  execution and read-only inspection, plus instruction files for agents.
  Execution commands (native `run`/`recover`, differential `execute`) run
  exactly the plan file given and record its SHA256. The agent shows the
  researcher the plan's parameters and hash in the chat and runs it after the
  researcher agrees; no approval file is needed.
- **DeepCPS** (`modules/deepcps`): optional annotation of called sites with
  DeepCPS scores.

## Installation

scPolyA is tested on Linux with Python 3.11.

### Python environment

```bash
git clone https://github.com/JeanWen-Lab/scPolyA.git
cd scPolyA
python3.11 -m venv .venv
source .venv/bin/activate
PIP_CONSTRAINT="$(pwd)/requirements/native-build-constraints.txt" \
    pip install -r requirements/native-runtime.txt
pip install ".[gui,test]"
```

`umi_tools` 1.1.6 is built from source. `PIP_CONSTRAINT` pins the build tools
(`setuptools`, `wheel`) in pip's isolated build environment; without it the
build can fail. With [uv](https://docs.astral.sh/uv/), the equivalent is
`uv pip install -r requirements/native-runtime.txt --build-constraints requirements/native-build-constraints.txt`.
`pip install .` installs the `scpolya-agent`, `scpolya-operator` and
`scpolya-diff-apa` commands. Run the pipeline and the GUI from the repository
root.

### External tools

The pipeline calls these programs, which must be on `PATH`:

- `samtools`
- `bedtools`
- `featureCounts` (Subread)
- `bedToBigBed` (UCSC)
- `bedGraphToBigWig` (UCSC)
- `umi_tools` (installed via `requirements/native-runtime.txt`)
- GNU coreutils `sort`

`main.py run` checks for the first six at start-up. They can be installed with
conda, for example:

```bash
conda install -c bioconda -c conda-forge \
    samtools bedtools subread ucsc-bedtobigbed ucsc-bedgraphtobigwig
```

## Quick start: `run`

Prepare a CSV listing samples and BAM files:

```csv
sample_id,source_path
donor1,/data/donor1/possorted_genome_bam.bam
donor2,/data/donor2/possorted_genome_bam.bam
```

Then run the pipeline:

```bash
python main.py run \
    --output-dir /data/scpolya-output \
    --run-id pbmc-example \
    --species human \
    --inputs samples.csv \
    --gene-gtf /refs/genes.gtf \
    --chrom-sizes /refs/genome.chrom.sizes \
    --merge-distance 10 \
    --unscaled-depth-threshold 1 \
    --unscaled-depth-threshold-polyA 1 \
    --summarize-matrix
```

`--merge-distance` and a peak threshold for both expression and poly(A) peaks
are required. The values above are demonstration values for fixed, unscaled
thresholds, not recommendations; dynamic thresholds are selected with
`--exp-peak-threshold-mode dynamic` / `--polyA-peak-threshold-mode dynamic`.
`--summarize-matrix` produces count matrices and summary tables; `--count-matrix`
produces the per-sample count matrices alone.
Terminal-aware APA metrics additionally require
`--quantification-mode terminal-aware` (the default is `legacy`).

Re-running the same command resumes from the last completed task. See all
options with:

```bash
python main.py run --help
python main.py prepare-bulk --help
python main.py --version
```

### Outputs

All outputs are written under `<output-dir>/<run-id>/`. Directory names such as
`pk-…`, `an-…` and `mx-…` are short configuration hashes, and `<sample>.<dedup>`
is the sample ID and UMI-dedup method (for example `donor1.directional`).

| Written when | Location and file names |
| --- | --- |
| always | `final_output/pk-…/pk-….___peaks-mispriming-filtered.bed` (plus `-merged.bed` and `-not-evaluable.audit.bed`); `final_output/pk-…/an-…/pk-….___peaks-annotated.bed`, `.bb`, `.gtf`; strand-specific bedGraph/BigWig coverage tracks under `cohort_output/` |
| `--count-matrix` or `--summarize-matrix` | `count_matrix/pk-…/an-…/<sample>.<dedup>.annotated.counts.tsv.gz`: one long table per sample with `peak_name`, `cell_barcode`, `umi_count`; plus per-PAS assignment diagnostics `….peak_assignment.tsv.gz` and `….peak_assignment_summary.json` |
| `--summarize-matrix` (default `legacy` profile) | `matrix_summary/pk-…/an-…/mx-…/`: `an-….peak_to_gene.tsv.gz`, and per sample `<sample>.<dedup>.annotated.gene_cell.tsv.gz`, `….cell_gene.matrix.tsv.gz`, `….psi.tsv.gz`, `….weighted_three_prime_utr_length.tsv.gz` |
| `--summarize-matrix --quantification-mode terminal-aware` | the same tables as `terminal_aware_*.v2.tsv.gz`, plus `intronic_ipui_per_site.v2.tsv.gz`, `intronic_ipui_gene_total.v2.tsv.gz`, the distal-site usage table `terminal_aware_distal_site_usage.v1.tsv.gz` and `terminal_aware_feature_overview.v1.tsv.gz`; the annotated-peak, count and diagnostic files above use `.terminal-aware.v2` names, and the filtered-peak BED becomes `___peaks-mispriming-audited.terminal-aware.v1.bed` |
| `--rna-gene-matrix` | exon-based `rna_gene_cell.tsv.gz` (optionally `rna_cell_gene.matrix.tsv.gz`) |

Distal-site usage is computed only in the terminal-aware profile. Logs and
`invocation.json` records are under `logs/`, and the task database is
`state/state.sqlite`.

### Optional bulk 3'UTR windows

`prepare-bulk` derives gene-locus and 3'UTR window GTFs from stranded bulk
coverage bigWigs. Pass its outputs to `run` with `--bulk-window-gtf` and
`--bulk-gene-gtf`:

```bash
python main.py prepare-bulk \
    --plus-bw bulk.plus.bw --minus-bw bulk.minus.bw \
    --gene-gtf /refs/genes.gtf \
    --output-gene-gtf bulk.gene.gtf --output-window-gtf bulk.window.gtf
```

## GUI

```bash
pip install ".[gui]"
python -m scpolya_ui --host 127.0.0.1 --port 24120 --no-browser
```

Then open <http://127.0.0.1:24120/>. The GUI keeps its own records in
`~/.scpolya-ui` (override with `SCPOLYA_UI_HOME`) and does not copy pipeline
data. Runs started from the GUI execute `main.py`, which writes into the run
root as usual. See [scpolya_ui/README.md](scpolya_ui/README.md) for exactly
what the GUI writes and where.

## Optional DeepCPS annotation

DeepCPS is off by default. To enable it, install the optional CPU inference
dependency and pass the model directory and the indexed alignment FASTA:

```bash
pip install -r requirements/deepcps-runtime.txt
python main.py run ... \
    --deepcps \
    --reference-fasta /refs/genome.fa \
    --deepcps-model-dir models/deepcps
```

The DeepCPS model weights and calibration tables are included in
[`models/deepcps/`](models/deepcps) (about 6 MB, MIT License). See
[docs/deepcps_integration.md](docs/deepcps_integration.md).

## Differential APA

`scpolya-diff-apa` compares one cell type between two conditions at the donor
level. `prepare` aggregates the raw per-sample PAS×cell UMI counts of one cell
type into donor pseudobulks, using cell-type and condition metadata that you
supply. `run` tests PAS composition with edgeR and length with limma in R.

```bash
scpolya-diff-apa prepare \
    --manifest manifest.tsv --metadata metadata.tsv --annotation common.tsv.gz \
    --cell-type CD8T --outdir results/CD8T-prepared

scpolya-diff-apa run \
    --input results/CD8T-prepared --outdir results/CD8T-CASE-minus-CTRL \
    --control CTRL --case CASE \
    --rscript "$(command -v Rscript)" --r-library /path/to/R-library
```

The R backend needs R with edgeR, limma and statmod installed; tested versions
are listed in [requirements/differential-apa-runtime.json](requirements/differential-apa-runtime.json).
See [docs/differential_apa.md](docs/differential_apa.md).

## Tests

```bash
python -m pytest -o pythonpath=. tests
```

Tests that need external tools (`featureCounts`, `samtools`, UCSC `liftOver`)
skip when the tools are absent. Set `SCPOLYA_LIFTOVER` to a `liftOver` binary
to run the coordinate-transform tests. The DeepCPS tests need the optional torch
dependency (`requirements/deepcps-runtime.txt`), and the differential APA R tests
are opt-in (`SCPOLYA_DIFF_R_LIBRARY`, `SCPOLYA_AGENT_DIFF_ACTUAL_R`). The bounded
coverage tools run only inside a systemd cgroup with a finite CPU quota and with
`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
`NUMEXPR_NUM_THREADS`, `NUMBA_NUM_THREADS` and `VECLIB_MAXIMUM_THREADS` set to
1; outside that environment their tests fail with a runtime refusal.

## Documentation

- [APA metrics](docs/apa_metrics.md)
- [Differential APA](docs/differential_apa.md)
- [DeepCPS integration](docs/deepcps_integration.md)
- [Agent extensions](docs/agent-extensions.md)
- [Agent tools](scpolya_agent/README.md)

## Citation

If you use scPolyA, please cite:

> <CITATION PLACEHOLDER>

## License

scPolyA, including the DeepCPS module and the DeepCPS model weights, is released under the [MIT License](LICENSE).
Copyright (c) 2026 The Wen Lab.
