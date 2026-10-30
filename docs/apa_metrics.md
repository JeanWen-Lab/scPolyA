# Native APA metrics

These definitions apply to opt-in `--quantification-mode terminal-aware`.
The default remains `legacy`, with its existing files and formulas.
The earlier annotation identity was `terminal-annotation-v3`; new runs use
`terminal-annotation-v4`, which only relabels gene-assigned PASs outside the
CDS canonical model and leaves every metric below unchanged. The existing count,
annotation BED and summary **filename suffixes** still contain `v2`. Those are
separate identities. This addition preserves both and adds a distal table
with its own `v1` filename suffix.

Each file below has the existing sample-specific prefix in the resolved matrix
summary configuration directory. A terminal gene unit is `gene_id + strand`,
never the display name. `terminal_gene_unit_id` percent-encodes each component
and joins them with `|`. Different genes or strands with the same display name
remain separate.

| Requested metric | File suffix / value column | Definition |
| --- | --- | --- |
| Site usage (PSI) | `terminal_aware_psi.v2.tsv.gz` / `psi` | `n_i / U` for an emitted terminal PAS/cell count row |
| Distal-site usage | `terminal_aware_distal_site_usage.v1.tsv.gz` / `distal_site_usage` | `sum(n_i for all maximally distant eligible terminal PASs) / U` |
| Usage-weighted 3′UTR length | `terminal_aware_weighted_three_prime_utr_length.v2.tsv.gz` / `weighted_three_prime_utr_length` | `sum(n_i * max(distance_to_stop_codon_i, 0)) / U` |
| Gene-total iPUI | `intronic_ipui_gene_total.v2.tsv.gz` / `total_iPUI` | `log2((I_total + 1) / (U + 1))` |

The additional `intronic_ipui_per_site.v2.tsv.gz` reports **per-intronic-PAS**
`iPUI_i = log2((I_i + 1) / (U + 1))`. It is a separate variant of iPUI,
not a substitute for gene-total iPUI. Count matrices, gene-cell tables and
peak-to-gene audit metadata support these metrics.

## Catalogue and denominators

Eligible terminal PASs have feature `Terminal_exon` or
`Downstream_of_terminal_exon`, a nonblank true gene ID, and an evaluable
mispriming ratio passing the general threshold (`ratio <= threshold`). The
existing implementation excludes missing ratios. For each gene/strand/cell,
`U` (`gene_umi_count` in terminal tables) is the raw UMI sum over **all these
eligible terminal PASs**. Intronic, other-exon, intergenic and failed terminal
counts do not enter U; neither do the v4 labels for gene-assigned PASs outside
the CDS canonical model (`Noncoding_exon`, `Noncoding_gene_body`,
`Noncanonical_exon`, `Noncanonical_gene_body`, `Canonical_model_edge`), which
are excluded exactly as `Intergenic` was. Overview tables group `Intergenic`
and these five labels as **Other** (`utils/feature_labels.py`); the group is
display-only, and site-level outputs keep each label (see *Feature overview*
below). Site usage, distal usage and weighted length require
at least two eligible terminal PASs and the configured `--psi-min-gene-umis`.
Distal usage additionally requires U > 0, including when the threshold is zero.

Intronic PASs pass their independent `--intronic-misprimed-ratio-threshold`.
iPUI requires an eligible terminal catalogue and an observed unit, but can be
defined with a single terminal PAS or with **U = 0**. Observed units come from
the union of terminal and intronic count records for the same gene and strand;
they do not require nonzero terminal counts. Existing intronic audit rows,
failed-site NA behavior and pseudocount formulas are unchanged.

Weighted length uses a clipped **genomic distance** to the representative
stop-codon annotation, not the spliced mature transcript length. Distal ordering
uses the original signed `distance_to_stop_codon`, before clipping. The upstream
annotation already supplies strand-oriented distances; the summary does not
sort genomic coordinates or pick a second strand-dependent extreme.

## Distal v1 columns and ties

The new table contains:

| Columns | Meaning |
| --- | --- |
| `gene_id`, `gene_name`, `strand`, `terminal_gene_unit_id` | Stable identity and deterministic display label from terminal/intronic audit metadata |
| `cell_barcode` | Observed unit; `NA` only for a catalogue audit row without any observed unit |
| `retained_terminal_pas_count` | Number of eligible terminal PASs, including eligible unobserved PASs |
| `distal_pas_count` | Number of PASs tied at maximum signed distance; `NA` if any eligible distance is missing or nonfinite |
| `distal_umi_count` | Sum of counts across **all** such tied distal PASs |
| `gene_umi_count` | Eligible terminal denominator U |
| `psi_min_gene_umis` | Threshold used by this summary |
| `distal_site_usage` | Fraction, or literal `NA` when unavailable |
| `usage_status` | `ok`, or the reason below |

All eligible PASs at the maximum distance are marked distal by the existing
metadata. Their counts are **summed**, never resolved by choosing one PAS.
For counts `(proximal=2, distal_a=1, distal_b=3)` at distances `(100, 200, 200)`,
U = 6, `distal_pas_count = 2`, `distal_umi_count = 4`, and usage = 4/6.
If all eligible PASs tie, usage is 1 for a sufficiently supported unit.
The original metadata flags and other exported tables remain unchanged.

## Coverage, zeros and unavailable values

The new table is sparse. It emits one row for each observed
`gene_id + strand + cell_barcode` pair found in terminal/intronic **audit**
count records (including failed PASs), restricted to valid true gene IDs and
`+`/`-` strands. It also emits one blank-cell audit row for each terminal/intronic
gene/strand with no observed unit. It does **not** expand genes across every
cell in the sample. An entirely empty catalogue yields a header-only table.
Missing gene IDs/invalid strands do not define a distal unit and remain visible
only in the existing peak audit. Other-exon-only cells are outside this table.

Within an observed unit and an eligible catalogue, an absent sparse terminal
PAS count is zero. Thus U > 0 with no distal counts yields **0**, provided the
unit passes the PAS count and U thresholds. A missing output row is outside
the exported coverage and must **never** automatically be filled with zero.
This rule assumes the supplied native counts matrix is a complete sparse
export for its declared input scope; the summary cannot detect a truncated
source matrix or infer cells omitted upstream.

Unavailable `distal_site_usage` is `NA`. Status precedence is:

1. `no_eligible_terminal_pas`: no passing terminal catalogue;
   `retained_terminal_pas_count` and `distal_pas_count` are 0, while U, numerator
   and usage are NA.
2. `fewer_than_two_terminal_pas`: one eligible PAS; usage is NA even with counts.
3. `no_observed_unit`: no terminal/intronic observation; blank cell and NA counts.
4. `missing_terminal_distance`: any eligible terminal distance is missing or
   nonfinite; distal PAS count/numerator/usage are NA, U remains available.
5. `zero_terminal_umis`: observed unit, eligible catalogue, U = 0; usage is NA.
6. `below_min_gene_umis`: positive U below the configured threshold; usage is NA.
7. `ok`: usage is available, including a measured zero distal fraction.

PSI and weighted-length v2 keep their existing sparse coverage and serialization;
they do not gain new audit rows or status columns. Their missing rows likewise
must not be interpreted as measured zeros. This addition does not repair or
redefine historical v2 edge-case behavior (for example explicit zero-count
records at a zero threshold).

## Which reads are counted, and which PASs

All counts above come from `featureCounts` assignment of deduplicated reads to
the counting GTF. Under the default `--count-read-rule legacy`, discovery and
counting use different read rules: the poly(A) extract keeps mapped reads with
MAPQ >= `--minMAPQ`, expression coverage keeps every read, and counting drops
every NH>1 read (Cell Ranger rescued multimappers with MAPQ 255 included) and
every read whose aligned blocks touch two PAS windows. A well-supported PAS
can therefore have **zero** counts although its reads exist. With
`--count-read-rule unified --multimapper-policy unique|rescued`, discovery and
counting share one rule (mapped, primary, MAPQ, multimapper policy); each
read is counted at most once, by default at the window holding its 3' end
(`--count-assignment three_prime_end`; `overlap` keeps overlap placement).
The RNA gene matrix and UMI/UPM scaling keep the historical read handling.
The read-rule options are listed under `python main.py run --help`.

`count_matrix/…peak_assignment…` reports, per PAS, how many overlapping reads
were assigned and why the rest were not, plus the share of multimapper
(NH>1 or `mm:i:1`) reads among overlapping and poly(A)-tail reads. A high
multimapper tail share flags paralog PASs (for example Hba-a1/a2) whose
support may not be unique. Read these diagnostics before interpreting a zero
or near-zero count.

The peak-to-gene table carries `included_in_counting` and `exclusion_reason`.
PASs that never entered the counting GTF (`no_same_strand_tail_support`, or
`non_intronic_misprimed_ratio_above_threshold` in terminal-aware mode) have no
counts by construction: they are not quantified, not measured zeros, and any
matrix row built for them must keep that flag. iPUI rows add
`ipui_support_status`: `audit_only` marks finite values that rest on I=U=0
(units observed only through failed intronic audit counts); such a 0 does not
show equal intronic and terminal use.

Non-standard contigs (and standard contigs left out by `--contigs` or
`--remove-mt`) never enter any table here; `input_tables/contig_read_report.tsv`
records how many alignments each contig held.

## Feature overview (display only)

Terminal-aware `summarize_matrix` writes
`terminal_aware_feature_overview.v1.tsv.gz` per sample, next to the other
summary tables. Columns: `sample_id`, `level` (`group`, `subcategory` or
`unmatched`), `group`, `feature`, `sites`, `mispriming_pass_sites`, `umis`.

- Group rows, in order: `Terminal_exon`, `Downstream_of_terminal_exon`,
  `Intron`, `Other_exon`, `Other`, then any other label found. `Other` is
  followed by six `subcategory` rows (`Intergenic` and the five v4 labels),
  which partition it. A site literally labelled `Other` gets its own
  `Other (site label)` group row.
- `sites`: unique `peak_name`s in the annotated site table with that label,
  before any filtering (the same rows as the peak-to-gene table).
- `mispriming_pass_sites`: sites whose `mispriming_pass` audit flag is true
  (general threshold; intronic threshold for `Intron`).
- `umis`: sum of `umi_count` over all cells in that sample's peak-cell count
  matrix (`count_matrix/…counts…tsv.gz`, the deduplicated UMI counts that
  summarize reads), for every site of the row, whatever its feature or filter
  state. Sites without counts add 0. The single `unmatched` row,
  `(not in site table)`, holds UMIs on count-matrix peaks that are absent from
  the site table (normally 0), so group rows plus it equal the matrix total
  of the rows summarize reads (rows with a missing or non-numeric count or a
  missing peak name are dropped, as in the metric path).

The table is written after the metric tables and nothing reads it back: it
does not change any count, filter, eligibility or metric file. Like distal v1,
a missing overview file forces the summary batch to recompute (below); the
metric files it rewrites are unchanged for unchanged inputs. Legacy mode does
not write it.

## Summary reuse and interfaces

Distal v1 and feature overview v1 are required by terminal summary
artifact-health checks. A DONE record from an earlier version does not
establish that they exist: when any configured sample lacks either file, `summarize_matrix` forces the summary batch to
recompute. This also applies with optional hybrid artifact-health gating
disabled. Other stages are not relaunched. If every distal and overview file exists, the
usual completion/reuse policy applies. The upgrade can recompute all samples
in the summary batch even if only one file is missing.

The new gzip export is written to a temporary sibling and renamed atomically;
an interrupted write is not published or marked DONE by the worker. Existing
summary files and coordinate signatures are preserved. There is no GUI change;
consumers can use the new file and its status column after resolving the sample
and matrix configuration in the usual way. Legacy runs neither produce nor
require it. These outputs are descriptive and make no claim of biological
validation.
