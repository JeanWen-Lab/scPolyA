---
name: scpolya-explain-export
description: Explain verified native scPolyA outputs, compute the descriptive evidence a question needs from native outputs and BAMs, and export source-cited results or GUI handoff materials without inventing or reinterpreting scPolyA model scores.
---

# Explain and export verified native evidence

Use the explicit interpreter and checkout paths supplied for this run:

```bash
PY=/absolute/python
SRC=/absolute/sc-polya-checkout
```

Start from a completed scPolyA run. Prefer a native `succeeded_verified` run;
for other existing outputs (for example a run the researcher made earlier),
check what state was recorded and say so when you report. Confirm actual
capabilities with `"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" bridge`. If the separate
`scpolya-analysis` skill is installed, you may use it for biological
interpretation.

Use existing read-only product/output operations rather than proposed wrapper
names. In chat, return compact views and references. When the researcher needs
matrices or tables as the deliverable, provide the native output files (or
copies/conversions of them) and say where they are. Preserve
zero as observed, distinguish source absence, unavailable evidence, and an
unsupported request, and retain every value's site catalogue, filters, genome
build, model/version state, and scope.

Resolve a gene through the run's annotation before quoting sites. Report
coordinates, strand, representative transcript/context, support fields, internal
priming fields, and model scores only when present. Multiple sites do not
automatically mean tandem terminal APA; intronic and terminal eligible sites
need separate denominators. Atlas proximity and model scores are support, not
ground truth.

For export, write only to a user-provided new output directory. Include a small
markdown/table report, exact source references, resolved configuration, native
run/plan identity, reuse counts, limitations, and GUI handoff instructions for
the standalone browser.

Compute the descriptive evidence the question needs from native outputs, the
input BAMs and the supplied references, for example sums of native counts by
cell type or label (pseudobulk), poly(A)-tail read-end positions and their
spread, alignment uniqueness (NH and multimapper tags), distances to annotated
transcript ends or atlas sites, upstream signal and downstream A content at the
read ends, and reproducibility across cell subsets. State how each value was
computed and keep the scripts with the outputs. Do not invent, recalibrate or
reinterpret scPolyA or DeepCPS model scores, do not silently change native
results or cleavage coordinates, and do not claim biological mechanism or
statistical significance without a test suited to the design. If a ranking
from a native operation orders sites by expression rather than by the evidence
the question asks about, add the relevant evidence before recommending sites.
If matching evidence is absent, say “not testable” and stop.

Name ordinary source/run paths and exported report/table files explicitly in GUI handoffs; no installed GUI deep-link route is implied.
