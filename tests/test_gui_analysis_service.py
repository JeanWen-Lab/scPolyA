"""Cross-file controls for complete exports, exact readers and closure binding."""
from pathlib import Path
import csv,hashlib,io,json,zipfile
import pytest
from scpolya_ui.services import analysis_service as service


@pytest.fixture
def saved(tmp_path,monkeypatch):
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(tmp_path/'tasks'))
    target=service.root()/'A-1234567890abcdef';(target/'analysis').mkdir(parents=True)
    service.emit(target/'task.json',dict(unit='scp-gui-analysis-a-1234567890abcdef',root=str(service.root())))
    service.emit(target/'plan.json',dict(schema='synthetic_reader_control'))
    path=target/'analysis/per-cell.tsv'
    with path.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['barcode','literal','value'],delimiter='\t',lineterminator='\n');writer.writeheader()
        writer.writerows(dict(barcode=str(i).zfill(4),literal='G"literal"',value='NA' if i%2 else '0') for i in range(601))
    table=dict(path=path.name,name='cells',format='tsv',columns=['barcode','literal','value'],row_count=601,bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    index=dict(method='cs1.fixed_coverage_full',summary=dict(n_cells=601),tables=[table],limitations=[])
    service.emit(target/'analysis/index.json',index)
    return target,table,index


def test_complete_export_601_rows_and_actual_quote_roundtrip(saved):
    target,table,index=saved
    service.package_report(target,index)
    raw=service.download_bytes(target)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows=list(csv.DictReader(io.StringIO(archive.read('per-cell.tsv').decode()),delimiter='\t'))
        assert len(rows)==601 and rows[0]['literal']=='G"literal"'
        assert 'GUI-MANIFEST.json' in archive.namelist()
    page=service.table_page(target,table,25)
    assert len(page)==1 and page[0]['barcode']=='0600'
    assert page[0]['value']=='0'


def test_download_detects_tampering_after_result_page_render(saved):
    target,table,index=saved;service.package_report(target,index)
    path=target/'report.zip';path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='export changed'):service.download_bytes(target)
    path=target/'analysis/per-cell.tsv';path.write_bytes(path.read_bytes().replace(b'G""literal',b'H""literal'))
    with pytest.raises(ValueError,match='table changed|Table size'):service.table_page(target,table)


def test_result_paths_cannot_escape_task(saved):
    target,_,_=saved
    with pytest.raises(ValueError):service.output_path(target,'../../outside')
    with pytest.raises(ValueError):service.output_path(target,'/tmp/outside')
    with pytest.raises(ValueError):service.task(target.parent/'not-a-task')


def test_launch_code_graph_detects_wrong_digest_without_modifying_checkout():
    records=service.code_sources();assert records
    records[0]=dict(records[0],sha256='0'*64)
    with pytest.raises(ValueError,match='code changed'):service.verify_code(records)
