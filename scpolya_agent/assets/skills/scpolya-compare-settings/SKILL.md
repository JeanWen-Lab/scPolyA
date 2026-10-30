---
name: scpolya-compare-settings
description: Compare a small, explicit native scPolyA setting change using matched run scopes, actual product operations, and honest unavailable-versus-observed reporting.
---

# Compare native settings without inventing science

Use the explicit interpreter and checkout paths supplied for this run:

```bash
PY=/absolute/python
SRC=/absolute/sc-polya-checkout
```

Bind the installation first with `"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" bridge`. Confirm which
product operations and output readers are actually available; do not turn a
proposal name into a command. If the separate `scpolya-analysis` skill is
installed, you may use it for interpretation.

Start with the comparison that answers the question, usually one named pair such
as native `minpolyA` 5 versus 10, or `unscaled_depth_threshold_polyA` 3 versus
1, while holding inputs, scope, contigs, genome reference, and runtime settings
fixed. Add arms or settings when the question needs them, and explain why.
The tail threshold is per-base poly(A)-tail coverage, not a count of tail reads
in a cluster.

Create each arm through a separate native plan and output root. Preserve both
plan hashes and materialized configurations. Before running, show the
researcher the parameters of both arms side by side (scope, the changed
setting and its values, other key settings, resources, output roots, and each
`plan_sha256`) and run them only after the researcher says yes in the chat. If native reuse machinery is
available, ask it what is reusable; never edit a task database or claim reuse
from file presence. Record tasks executed, reused, unavailable, and failed.

Compare only matched scopes and report the numerator, eligible denominator, and
unavailable count for every metric. Separate multi-site genes from genes with
multiple eligible terminal sites. Missing evidence, source absence, zero, and
an unsupported operation are distinct states. Descriptive evidence computed
from native outputs and BAMs (for example read-end positions, alignment
uniqueness, sequence context, distances to annotation, pooled counts) is
welcome; state how it was computed. Do not invent scPolyA model scores, and do
not claim significance, FDR or an “optimum” without a test suited to the
design (one library has no biological replicates). A higher yield is not proof
of better accuracy.

Keep the QC cell set and the pooled-versus-per-cell level of each value explicit in interpretation. If evidence a claim needs is unavailable, say so rather than fabricating it.
