"""Preset selection must preserve scientific roles, both strands and all slots."""
from scpolya_ui.igv_manifest import is_core_track, manifest_as_igv_options


def test_core_keeps_both_strands_both_runs_and_historical_bed_only():
    tracks = [
        {'role': role, 'format': fmt, 'name': f'{slot} {role} {strand}',
         'url': f'/{slot}/{role}/{strand}.{fmt}', 'type': 'annotation'}
        for slot in ('A', 'B') for strand in ('+', '-')
        for role, fmt in [('coverage_scrna', 'bigwig'), ('coverage_polya', 'bigwig'),
                          ('annotated_peak', 'bed'), ('raw_peak_scrna', 'bed'),
                          ('historical_annotation', 'bed'), ('historical_annotation', 'gtf'),
                          ('transcript_model', 'gtf'), ('variant_peak', 'bed')]
    ]
    options = manifest_as_igv_options({'tracks': tracks})['tracks']
    assert len({t['scpolyaKey'] for t in options}) == len(tracks)
    for original, option in zip(tracks, options):
        assert option['url'] == original['url'] and option['name'] == original['name']
        assert option['scpolyaCore'] == (original['role'] != 'raw_peak_scrna' and
                                         not (original['role'] == 'historical_annotation' and original['format'] == 'gtf'))
    assert not is_core_track({'role': 'unknown'})
