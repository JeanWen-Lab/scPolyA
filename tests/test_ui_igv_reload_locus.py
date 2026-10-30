"""Execute the production JavaScript's coordinate conversion, with no browser I/O."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scpolya_ui.igv_manifest import coordinate_locus
from scpolya_ui.shell.runtime import PRODUCT_RUNTIME_HEAD


@pytest.fixture(scope='module')
def frame_loci():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required to execute the production IGV coordinate helper')

    def run(cases):
        script = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const ctx={window:{addEventListener(){}},document:{addEventListener(){},documentElement:{}},
 MutationObserver:class{observe(){}},setTimeout(){}};
vm.runInNewContext(data.runtime,ctx);
const result=data.cases.map(c=>{
 if(c.nonfinite)c.frame[c.nonfinite[0]]=({nan:NaN,inf:Infinity,negInf:-Infinity})[c.nonfinite[1]];
 return ctx.window.scpolyaProductIgvReloadLocus({referenceFrameList:c.frames||[c.frame],genome:{getChromosome:()=>c.chromosome}});});
process.stdout.write(JSON.stringify(result));
'''
        runtime = PRODUCT_RUNTIME_HEAD.split('<script>')[1].split('</script>')[0]
        result = subprocess.run([node, '-e', script], input=json.dumps({'runtime': runtime, 'cases': cases}),
                                text=True, check=True, capture_output=True)
        return json.loads(result.stdout)

    return run


def _case(start, end, length=1000, chr='chr4'):
    return {'frame': {'chr': chr, 'start': start, 'end': end},
            'chromosome': {'name': chr, 'bpLength': length}}


def test_outward_rounding_covers_partial_bases_and_preserves_integer_edges(frame_loci):
    cases = [_case(100, 200), _case(100.25, 199.2), _case(0.25, 0.75),
             _case(99.999, 100), _case(100, 100.00001)]
    assert frame_loci(cases) == ['chr4:101-200', 'chr4:101-200', 'chr4:1-1', 'chr4:100-100', 'chr4:101-101']


def test_clips_only_to_known_contig_boundaries_and_rejects_empty_intersections(frame_loci):
    cases = [_case(-5, 10.25, 100), _case(95.2, 105, 100), _case(-5, 105, 100),
             _case(100, 110, 100), _case(-10, 0, 100), _case(10, 10), _case(11, 10)]
    assert frame_loci(cases) == ['chr4:1-11', 'chr4:96-100', 'chr4:1-100', '', '', '', '']


def test_reference_contig_names_and_first_view_are_preserved(frame_loci):
    cases = [_case(9, 20, chr=name) for name in ('chrM', 'GL000220.1', 'chrUn_GL456239', '1')]
    first = _case(9, 20)
    first['frames'] = [first['frame'], {'chr': 'chr17', 'start': 100, 'end': 200}]
    cases.append(first)
    alias = _case(9, 20, chr='4')
    alias['chromosome']['name'] = 'chr4'  # Canonical FASTA contig from IGV's genome.
    cases.append(alias)
    assert frame_loci(cases) == ['chrM:10-20', 'GL000220.1:10-20', 'chrUn_GL456239:10-20',
                                 '1:10-20', 'chr4:10-20', 'chr4:10-20']


def test_invalid_frames_and_unknown_or_virtual_contigs_do_not_generate_requests(frame_loci):
    cases = [_case(None, 10), _case(0, None), _case('1', 10), _case(0, 10, 0),
             _case(0, 10, 100.5), _case(0, 10, 2**53), _case(0, 10, chr='all'),
             _case(0, 10, chr='chr 4'), _case(0, 10, chr='chr4:1')]
    unknown = _case(0, 10)
    unknown['chromosome'] = None
    cases.extend([unknown, {'frames': [], 'chromosome': None}, _case(0, 10, '1000')])
    for axis, kind in [('start', 'nan'), ('start', 'inf'), ('end', 'inf'), ('end', 'negInf')]:
        case = _case(0, 10)
        case['nonfinite'] = [axis, kind]
        cases.append(case)
    assert frame_loci(cases) == [''] * len(cases)


def test_user_coordinate_parser_remains_integer_only_and_accepts_other_contigs():
    assert coordinate_locus('chr4:100.25-200') is None
    assert coordinate_locus('chr4:100-200.25') is None
    for name in ('chrM', 'GL000220.1', 'chrUn_GL456239', '1'):
        locus = coordinate_locus(f'{name}:1,000-2,000')
        assert (locus.chrom, locus.start, locus.end) == (name, 1000, 2000)


def test_reload_view_is_exact_and_bound_without_changing_normal_peak_focus():
    from scpolya_ui.igv_manifest import GeneLocus, with_view_window

    ordinary = GeneLocus('chr4:1000-9000', 'chr4', 1000, 9000, focus_start=4000, focus_end=4100)
    assert ordinary.igv_locus == 'chr4:3500-4600'
    assert 'viewport_locus' not in ordinary.as_dict()
    assert with_view_window(ordinary, None) is ordinary
    reloaded = with_view_window(ordinary, coordinate_locus('chr4:1000-9000'))
    assert reloaded.igv_locus == 'chr4:1000-9000'
    assert reloaded.as_dict()['viewport_locus'] == reloaded.igv_locus
    assert (reloaded.start, reloaded.end, reloaded.focus_start, reloaded.focus_end) == (1000, 9000, 4000, 4100)
    for wrong in ('chr17:1000-9000', 'chr4:1001-9000', 'chr4:1000-9001'):
        with pytest.raises(ValueError, match='does not match'):
            with_view_window(ordinary, coordinate_locus(wrong))


def test_reload_transcripts_cover_view_edges_instead_of_only_the_peak_cluster(tmp_path):
    from dataclasses import replace
    from scpolya_ui.igv_manifest import GeneLocus, IgvManifest, with_transcript_models, with_view_window

    gtf = tmp_path / 'source.gtf'
    rows = ''.join(f'chr4\tTEST\t{feature}\t{start}\t{end}\t.\t+\t.\tgene_id "G"; transcript_id "{tid}";\n'
                   for start, end, tid in [(1100, 1400, 'LEFT'), (8000, 8900, 'RIGHT')]
                   for feature in ('transcript', 'exon'))
    gtf.write_text(rows)
    ordinary = IgvManifest('', 'chr4:1000-9000', GeneLocus('chr4:1000-9000', 'chr4', 1000, 9000,
                                                       focus_start=4000, focus_end=4100), (), (),
                           reference_gtf=str(gtf))
    normal = with_transcript_models(ordinary, str, tmp_path/'normal')
    assert not normal.tracks  # Neither edge transcript intersects the peak focus.
    reloaded = replace(ordinary, locus=with_view_window(ordinary.locus, coordinate_locus(ordinary.gene)))
    result = with_transcript_models(reloaded, str, tmp_path/'reload')
    assert len(result.tracks) == 1
    text = Path(result.tracks[0].path).read_text()
    assert '"LEFT"' in text and '"RIGHT"' in text
    assert result.locus.igv_locus == 'chr4:1000-9000'
