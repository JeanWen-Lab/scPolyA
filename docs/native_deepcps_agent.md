# Native DeepCPS agent request and result contract

DeepCPS stays off by default. The existing `scpolya.native_task_request.v1`
request accepts these fields inside `run`:

```json
{
  "deepcps": true,
  "reference_fasta": "/references/alignment-reference.fa",
  "deepcps_model_dir": "/models/deepcps",
  "deepcps_model_manifest": "/models/deepcps-manifest.json",
  "deepcps_reference_sha256": "REPLACE_WITH_64_HEXADECIMAL_CHARACTERS",
  "deepcps_threads": 2,
  "deepcps_batch_size": 128
}
```

This is a fragment, not a complete runnable request. The other native run,
resource and input fields remain required. `deepcps` must be a JSON boolean;
threads must be an integer in 1–2 and batch size an integer in 1–512. The
manifest and reference SHA are optional. The default manifest is the one shipped
with this exact checkout. Explicit strings, zero, null or
out-of-range inference settings are refused rather than silently coerced.
Explicit `deepcps:false` becomes `--no-deepcps`. Omitted DeepCPS fields preserve
the previous command and default-off behavior. Disabled plans neither open
DeepCPS resource paths nor probe optional dependency versions.

The enabled plan has a `deepcps` identity recording the actual FASTA, existing
FAI and optional GZI, all nine model/calibration resources and manifest bytes,
source BAM reference-dictionary/M5 evidence, inference settings, implementation
hashes, an explicit checked/not-declared architecture identity, and versions of
torch/NumPy/pandas/pysam/pyBigWig reported by the requested
native Python. No torch/model import occurs in the plan process. Native helpers
perform the model/reference checks, including any supplied FASTA SHA. A declared
`architecture_source_sha256` must be well formed and equal the actual native
`modules/deepcps/model.py` byte digest; a custom manifest without it records
`not_declared` and still pins actual implementation bytes. Paths must
be absolute. Planning and run/recover admission read the full FASTA; this cost
scales with reference size and number of source BAMs. The producer separately
checks its generated cohort BAM before annotation. Available M5 digests are
verified; missing M5 still prevents independent proof of alignment base identity.

The whole-plan hash covers this identity. Run and recover recapture it before
creating an attempt, so same-path reference, index, model, manifest or dependency
version changes refuse admission. Existing source/runtime/input and
recovery/quiescence checks remain in force. Optional dependency versions are
recorded, not installed-code byte certification. Run and recover execute exactly
the hashed plan; this is integrity binding, not OS isolation.

The config-only preview reports the seven raw keys and projects their settings
into the existing DeepCPS fingerprint identity. It opens no scientific files;
same-path byte changes require planning/admission and are outside this preview.
Changed DeepCPS settings affect only the DeepCPS annotation task. The running
stage retains its full native content fingerprint; the preview signature is
never used as an execution/cache fingerprint.

After successful supervisor exit, the driver stores a per-attempt
`deepcps-result-seal.json` only after it verifies own invocation completion,
the DeepCPS completion observation, provenance, both complete TSVs and original
BED. Deep-enabled status remains `succeeded_unverified` until the completion seal and
its resource receipt are present and consistent. Failed closure is recorded in
attempt resources, preserves the native
scientific outcome, and leaves structured reading unavailable. The seal records
the original provenance and output digests; it is neither a signature nor an
OS security boundary. No result seal is generated lazily by a reader.

```bash
"$PY" -I -B "$SRC/native_entry.py" --source-root "$SRC" driver -- \
  deepcps-result --plan /jobs/native/plan.json --offset 0 --limit 100
```

`scpolya_agent.native_task_driver.deepcps_result_plan` implements the same reader
for programmatic callers. The reader needs own verified native completion and
confirmed quiescence. It selects the one DeepCPS fingerprint recorded for that
invocation and refuses absent, ambiguous, changed or unsealed results. It
verifies input-model/reference provenance against the plan, whole table hashes,
stable keys and native BED/join coordinates and order. An altered output whose
provenance hash was also rewritten still differs from the completion seal.

The result schema is `scpolya.native_deepcps_result.v1`. It includes the plan
hash, invocation/observation, content fingerprint, original provenance and digest,
whole-run `n_sites`/`n_scored`/reason histogram, plus paginated scored rows. Each
row retains its stable key, native identity, original row number, three seed
probabilities, raw/calibrated scores, localization values and explicit reasons.
Numeric zero remains zero; literal native NA values become JSON null with their
reason retained. A completed all-NA stage is distinguishable from an unavailable
result and never claims that every site was scored. The completion observation
alone does not distinguish fresh inference from verified reuse.

Scored rows require localization confidence and an integer offset within the
native ±100-base window. A present localized site must use `located` and equal
the called site plus the offset on `+`, or minus the offset on `-`. An NA localized
site requires `prediction_outside_reference` while retaining confidence and
offset. Unscored rows require all score/localization result values to be NA and
matching native score/localization reasons. These checks also run before worker
sealing. The reader does not reopen the reference or independently prove an
outside-reference boundary: current provenance records no chromosome lengths.

The reader returns at most 500 rows per call (default 100), accepts nonnegative
offsets and returns `next_offset` or null. It checks at most 64 MiB per table and
original BED, 1 MiB of provenance, and 256 output-directory entries. Larger runs
are explicitly refused by this bounded interface; their native sidecars remain
available for a separately scoped reader. No counting interval, score filter,
UMI weighting, localization policy or scientific inference code is changed.
Model weights are included in `models/deepcps/`.
