"""Headerless BED intervals survive display pagination and complete export."""
import hashlib
import io
import zipfile

from scpolya_ui.services import analysis_service as service


def test_bed_first_interval_page_boundary_and_export_bytes(tmp_path,monkeypatch):
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(tmp_path/'tasks'))
    target=service.root()/'A-1234567890abcdef'
    (target/'analysis').mkdir(parents=True)
    service.emit(target/'task.json',{'root':str(service.root())})
    service.emit(target/'plan.json',{'schema':'synthetic_bed_reader_control'})
    path=target/'analysis/intervals.bed'
    raw=''.join(f'chr1\t{i}\t{i+1}\tPAS"{i}"\t0\t-\n' for i in range(26)).encode()
    path.write_bytes(raw)
    table=dict(path=path.name,name=path.name,format='bed',columns=[],row_count=26,
               bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    first=service.table_page(target,table,1)
    second=service.table_page(target,table,2)
    assert len(first)==25 and len(second)==1
    assert first[0]==dict(chrom='chr1',chromStart='0',chromEnd='1',name='PAS"0"',score='0',strand='-')
    assert first[-1]['chromStart']=='24' and second[0]['chromStart']=='25'
    assert service.table_page(target,table,3)==[]
    service.package_report(target,dict(method='synthetic_reader',tables=[table],summary={},limitations=[]))
    with zipfile.ZipFile(io.BytesIO(service.download_bytes(target))) as archive:
        assert archive.read(path.name)==raw
    assert path.read_bytes()==raw


def test_bed12_and_extra_columns_do_not_drop_or_parse_literal_values(tmp_path,monkeypatch):
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(tmp_path))
    target=tmp_path/'A-1234567890abcdef';(target/'analysis').mkdir(parents=True)
    fields=['chr2','10','20','name','0','+','10','20','0','2','4,6','0,4','literal"extra"']
    path=target/'analysis/test.bed';raw=('\t'.join(fields)+'\n').encode();path.write_bytes(raw)
    rows=service.table_page(target,dict(path=path.name,format='bed',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    assert len(rows)==1
    assert rows[0]['blockSizes']=='4,6' and rows[0]['blockStarts']=='0,4'
    assert rows[0]['column_13']=='literal"extra"'


def test_liftover_unmapped_comments_and_page_boundary_keep_coordinates(tmp_path,monkeypatch):
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(tmp_path/'tasks'))
    target=service.root()/'A-1234567890abcdef';(target/'analysis').mkdir(parents=True)
    raw=''.join(f'#Deleted in new\nchr1\t{i}\t{i+1}\tPAS{i}\t0\t-\n' for i in range(16)).encode()
    path=target/'analysis/unmapped.bed';path.write_bytes(raw)
    table=dict(path=path.name,format='bed',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),row_count=32)
    first=service.table_page(target,table,1);second=service.table_page(target,table,2)
    columns=['chrom','chromStart','chromEnd','name','score','strand','comment']
    assert len(first)==25 and len(second)==7
    assert all(list(row)==columns for row in first+second)
    assert first[0]['comment']=='#Deleted in new' and first[0]['chrom']==''
    assert first[1]['chromStart']=='0' and first[1]['strand']=='-' and first[1]['comment']==''
    assert first[-1]['comment']=='#Deleted in new'
    assert second[0]['chromStart']=='12' and second[-1]['chromStart']=='15'
    service.emit(target/'task.json',{'root':str(service.root())});service.emit(target/'plan.json',{'schema':'synthetic_liftover_reader'})
    service.package_report(target,dict(method='synthetic_reader',tables=[table],summary={},limitations=[]))
    with zipfile.ZipFile(io.BytesIO(service.download_bytes(target))) as archive:assert archive.read(path.name)==raw
    assert path.read_bytes()==raw


def test_bed_variable_width_track_and_browser_lines_preserve_raw_text(tmp_path,monkeypatch):
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(tmp_path))
    target=tmp_path/'A-1234567890abcdef';(target/'analysis').mkdir(parents=True)
    lines=['track name="literal track"\tdescription="test"','browser position chr2:1-20',
           'chr2\t10\t11','chr2\t11\t12\twide-name\t0\t+']
    path=target/'analysis/variable.bed';raw=('\n'.join(lines)+'\n').encode();path.write_bytes(raw)
    rows=service.table_page(target,dict(path=path.name,format='bed',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    assert all(list(row)==['chrom','chromStart','chromEnd','name','score','strand','comment'] for row in rows)
    assert rows[0]['comment']==lines[0] and rows[1]['comment']==lines[1]
    assert rows[2]['chromStart']=='10' and rows[2]['name']=='' and rows[2]['strand']==''
    assert rows[3]['name']=='wide-name' and rows[3]['strand']=='+'
