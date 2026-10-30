# Read-only configuration impact preview

This additive CLI uses the existing native `build_effective_config_projection`
and `preview_rerun_from_params` APIs, with their union of effective keys, and
serializes the existing `RunConfigPreview` with `physical_output_impact=None`.
It accepts two saved resolved configuration objects (at most 1 MiB each), their
SHA256 pins, and an explicit complete trusted checkout with expected Git HEAD.
Tracked modifications are refused. Its source pins cover the loaded project
module dependency closure, including native package imports. The CLI is also
hashed separately. These pins are not a full runtime/environment identity or a
security sandbox.

```sh
/path/to/python -I -B /path/to/scpolya_agent/native_config_preview.py \
  --source-root /complete/trusted/checkout --expect-head COMMIT \
  --old old-resolved.json --old-sha256 SHA256 \
  --new new-resolved.json --new-sha256 SHA256
```

No native plan, execution, scientific-input scan, or live cache query is
performed. Input paths in configurations remain strings. Original `output_dir`
and `run_id` differences are retained in the raw configuration projections and
`changed_raw_keys`. Physical output naming/projection is explicitly `UNASSESSED`,
with nested `physical_output_impact=null`; configured input/output roots are
never inspected.
Impacted entries are native task types, not expanded tasks or execution results.
Unaffected types are not evidence of reusable artifacts. In the nested native
response, `full_cache_rebuild=false` alongside `cache_epoch=null` means the epoch
was not assessed. The explicit outer flags carry that limitation.

Historical configurations can be compared under the chosen current contract, but
this is not a plan admission check and does not rebind historical source identity.
The CLI is deliberately separate from the frozen installed product wrapper. Its
only purpose is a bounded read-only view of an existing native Python API.

The preview does not call the combined `preview_rerun_from_run_configs` API,
because its physical projection reads live naming manifests and stats output
paths. It uses existing native APIs that stay within the configuration-only
boundary; the native engine, pipeline contract rules and historical results are
unchanged.
