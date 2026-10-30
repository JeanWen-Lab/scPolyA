"""Read-only CLI over the native config-impact API; never plans or runs work."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

MAX_CONFIG_BYTES = 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def loaded_source_pins(root):
    """Pin the loaded project dependency closure, including package imports."""
    paths = {
        Path(module.__file__).resolve()
        for module in tuple(sys.modules.values())
        if getattr(module, '__file__', None)
        and Path(module.__file__).is_relative_to(root)
    }
    return {
        str(path.relative_to(root)): digest(path.read_bytes())
        for path in sorted(paths)
    }


def read_config(path, expected):
    path = Path(path)
    with path.open('rb') as stream:
        data = stream.read(MAX_CONFIG_BYTES + 1)
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError('config_exceeds_1MiB')
    if digest(data) != expected:
        raise ValueError('config_sha256_mismatch')
    config = json.loads(data)
    if not isinstance(config, dict):
        raise ValueError('config_must_be_object')
    return config, {'path': str(path.resolve()), 'sha256': digest(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--expect-head', required=True)
    for side in ('old', 'new'):
        parser.add_argument('--' + side, required=True)
        parser.add_argument('--' + side + '-sha256', required=True)
    args = parser.parse_args()
    try:
        root = Path(args.source_root).resolve()
        head = subprocess.check_output(
            ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True
        ).strip()
        if head != args.expect_head:
            raise ValueError('source_head_mismatch')
        if subprocess.run(
            ['git', '-C', str(root), 'diff', '--quiet', 'HEAD', '--'],
            capture_output=True,
        ).returncode:
            raise ValueError('tracked_source_dirty')
        old, old_pin = read_config(args.old, args.old_sha256)
        new, new_pin = read_config(args.new, args.new_sha256)
        sys.path.insert(0, str(root))
        from task_manage.run_config_preview import (
            RunConfigPreview, build_effective_config_projection,
        )
        from task_manage.rerun_preview import preview_rerun_from_params
        old_projection = build_effective_config_projection(old)
        new_projection = build_effective_config_projection(new)
        keys = list(dict.fromkeys([
            *old_projection.preview_params, *new_projection.preview_params,
        ]))
        preview = RunConfigPreview(
            old_projection=old_projection,
            new_projection=new_projection,
            rerun_preview=preview_rerun_from_params(
                old_projection.preview_params, new_projection.preview_params,
                keys=keys,
            ),
            physical_output_impact=None,
        ).as_dict()
        pins = loaded_source_pins(root)
        result = {
            'schema': 'scpolya.native_config_preview.v1', 'status': 'observed',
            'source_root': str(root), 'source_head': head, 'native_source_pins': pins,
            'cli_sha256': digest(Path(__file__).read_bytes()),
            'configs': {'old': old_pin, 'new': new_pin}, 'preview': preview,
            'scope': 'native contract/config impact only; no state/cache/artifact availability inspection',
            'actual_reuse_verified': False, 'native_execution': False,
            'cache_epoch_assessed': False,
            'physical_output_projection_status': 'UNASSESSED',
            'limitations': [
                'impacted task types are not expanded sample/contig task instances',
                'unaffected task types are not proof existing outputs are reusable',
                'separate output roots/config differences remain visible; no plan binding or approval created',
                'scientific input paths are strings only, never opened by this CLI',
                'physical output naming/projection is unassessed; configured input/output roots are not inspected',
                'full_cache_rebuild=false with cache_epoch=null means unassessed, not cache compatibility',
            ],
        }
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({
            'schema': 'scpolya.native_config_preview.v1', 'status': 'refused',
            'reason': str(exc), 'native_execution': False,
        }, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
