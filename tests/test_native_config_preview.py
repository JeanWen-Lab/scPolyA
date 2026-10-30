"""Config-only checks; syscall tracing detects even failed path accesses."""
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scpolya_agent/native_config_preview.py'

THRESHOLD_TASKS = {
    ('call_peaks', 'merge_call_peaks'), ('call_peaks', 'combine_and_intersect_peaks'),
    ('annotate_peaks', 'assign_peaks_to_genes'), ('annotate_peaks', 'merge_assigned_peaks'),
    ('annotate_peaks', 'classify_peaks'), ('annotate_peaks', 'resolve_peak_issues'),
    ('annotate_peaks', 'convert_peaks_bed_to_bigbed'), ('annotate_peaks', 'convert_peaks_bed_to_gtf'),
    ('deepcps', 'score_and_join'), ('annotate_bams', 'annotate_slices'),
    ('count_matrix', 'count_unit_matrix'), ('count_matrix', 'summarize_count_matrix'),
    ('summarize_matrix', 'summarize_matrix'),
}
POLYA_TASKS = THRESHOLD_TASKS | {
    ('extract_subsets', 'extract_polyA'), ('extract_subsets', 'split_strand'),
    ('calculate_umi', 'count_umi_polyA'), ('calculate_umi', 'calculate_umi'),
    ('merge_units', 'merge_chunks'), ('merge_units', 'cat_chunks'),
    ('merge_units', 'merge_strand_chunks'), ('merge_units', 'cat_strand_chunks'),
    ('call_peaks', 'generate_raw_coverage'), ('call_peaks', 'upm_scale_coverage'),
    ('call_peaks', 'exclude_cds_region'), ('call_peaks', 'negate_bedgraph'), ('call_peaks', 'generate_bigwig'),
}


def task_types(result):
    return {(x['stage'], x['task_base']) for x in result['preview']['impacted_tasks']}


class ConfigPreviewTests(unittest.TestCase):
    def invoke(self, old, new, *, bad_pin=False, bad_head=False, trace_roots=()):
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / name for name in ('old.json', 'new.json')]
            for path, value in zip(paths, (old, new)):
                path.write_text(json.dumps(value))
            head = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
            argv = [sys.executable, '-I', '-B', str(CLI), '--source-root', str(ROOT),
                    '--expect-head', '0' * 40 if bad_head else head]
            for side, path in zip(('old', 'new'), paths):
                pin = hashlib.sha256(path.read_bytes()).hexdigest()
                argv += ['--' + side, str(path), '--' + side + '-sha256',
                         '0' * 64 if bad_pin else pin]
            trace = Path(temp) / 'files.trace'
            if trace_roots:
                argv = ['strace', '-f', '-qq', '-yy', '-s', '4096', '-e',
                        'trace=%file,getdents64', '-o', str(trace), *argv]
            result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            if trace_roots:
                accesses = [line for line in trace.read_text().splitlines()
                            if any(root in line for root in trace_roots)]
                self.assertEqual(accesses, [], 'configured root access attempted')
            return result.returncode, json.loads(result.stdout)

    def config(self):
        return {'inputs': '/NEVER-OPEN/inputs.csv', 'gene_gtf': '/NEVER-OPEN/gene.gtf',
                'chrom_sizes': '/NEVER-OPEN/chrom.sizes', 'output_dir': '/NEVER-CREATE',
                'run_id': 'inert', 'minpolyA': 5, 'nonA_allowed': 1, 'minMAPQ': 10,
                'polyA_peak_threshold_mode': 'fixed', 'unscaled_depth_threshold_polyA': 3,
                'call_unscaled_polyA_peaks': True, 'merge_distance_polyA': 10}

    def test_unchanged_nonexistent_input_paths(self):
        code, result = self.invoke(self.config(), self.config())
        self.assertEqual(code, 0, result)
        self.assertEqual(result['preview']['impacted_tasks'], [])
        self.assertFalse(result['actual_reuse_verified'])
        self.assertFalse(result['cache_epoch_assessed'])
        self.assertIsNone(result['preview']['physical_output_impact'])
        self.assertEqual(result['physical_output_projection_status'], 'UNASSESSED')
        self.assertIn('task_manage/artifact_naming.py', result['native_source_pins'])
        self.assertIn('task_manage/run_layout.py', result['native_source_pins'])

    def test_threshold_change_uses_native_contract(self):
        old = self.config()
        code, result = self.invoke(old, dict(old, unscaled_depth_threshold_polyA=1))
        self.assertEqual(code, 0, result)
        types = {(x['stage'], x['task_base']) for x in result['preview']['impacted_tasks']}
        self.assertIn(('call_peaks', 'merge_call_peaks'), types)
        self.assertNotIn(('extract_subsets', 'extract_polyA'), types)
        self.assertEqual(types, THRESHOLD_TASKS)

    @unittest.skipUnless(shutil.which('strace'), 'strace required for syscall control')
    def test_no_attempted_access_to_nonexistent_roots(self):
        old = self.config()
        new = dict(old, minpolyA=10, output_dir='/NEVER-CREATE-NEW', run_id='new')
        code, result = self.invoke(old, new, trace_roots=('/NEVER-OPEN', '/NEVER-CREATE'))
        self.assertEqual(code, 0, result)
        self.assertEqual(task_types(result), POLYA_TASKS)
        self.assertIn('output_dir', result['preview']['changed_raw_keys'])
        self.assertIn('run_id', result['preview']['changed_raw_keys'])

    @unittest.skipUnless(shutil.which('strace'), 'strace required for syscall control')
    def test_no_attempted_access_to_existing_roots(self):
        with tempfile.TemporaryDirectory(prefix='preview-sentinel-') as temp:
            root = Path(temp)
            (root / 'inputs.csv').write_text('sentinel,never read\n')
            (root / 'inert/final_output').mkdir(parents=True)
            old = dict(self.config(), inputs=str(root / 'inputs.csv'), output_dir=temp)
            code, result = self.invoke(old, dict(old, minpolyA=10),
                                       trace_roots=(temp, '/NEVER-OPEN'))
            self.assertEqual(code, 0, result)
            self.assertEqual(task_types(result), POLYA_TASKS)

    @unittest.skipUnless(shutil.which('strace'), 'strace required for syscall control')
    def test_deepcps_preview_never_opens_reference_or_model_paths(self):
        old = dict(self.config(), deepcps=False)
        new = dict(old, deepcps=True, reference_fasta='/NEVER-DEEP/genome.fa',
                   deepcps_model_dir='/NEVER-DEEP/models', deepcps_model_manifest='/NEVER-DEEP/manifest.json',
                   deepcps_reference_sha256='0' * 64, deepcps_threads=1, deepcps_batch_size=7)
        code, result = self.invoke(old, new, trace_roots=('/NEVER-DEEP', '/NEVER-OPEN', '/NEVER-CREATE'))
        self.assertEqual(code, 0, result)
        self.assertEqual(task_types(result), {('deepcps', 'score_and_join')})

    def test_wrong_pin_refused(self):
        code, result = self.invoke(self.config(), self.config(), bad_pin=True)
        self.assertEqual((code, result['reason']), (2, 'config_sha256_mismatch'))

    def test_wrong_head_refused(self):
        code, result = self.invoke(self.config(), self.config(), bad_head=True)
        self.assertEqual((code, result['reason']), (2, 'source_head_mismatch'))

    def test_non_object_config_refused(self):
        code, result = self.invoke([], self.config())
        self.assertEqual((code, result['reason']), (2, 'config_must_be_object'))

    def test_oversized_config_refused(self):
        code, result = self.invoke({'oversized': 'x' * (1024 * 1024)}, self.config())
        self.assertEqual((code, result['reason']), (2, 'config_exceeds_1MiB'))


if __name__ == '__main__':
    unittest.main()
