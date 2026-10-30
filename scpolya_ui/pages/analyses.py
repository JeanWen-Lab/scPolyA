"""Ordinary deterministic-analysis form in the existing product shell."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from typing import Any
from nicegui import ui, run
from scpolya_ui.state.app_state import ProductAppState
from scpolya_ui.services import analysis_service as service
from scpolya_ui.services import analysis_json_service as json_service
from scpolya_ui.services import analysis_detail_service as details
from scpolya_ui.services import export_recovery as recovery
from scpolya_ui.services.workspace_service import get_workspace

PAGE_KEY='analyses'
FIELDS={
    'cs1.fixed_coverage_full':['sample_id','cohort','registry','selected_units','selection_receipt','group_a','group_b'],
    'cs3.transform':['source_table','chain','source_sizes','target_sizes','liftover','name','release','source_assembly','target_assembly','source_coordinate_base','coordinate_status','reference_population','source_provenance','source_scope','source_format','contigs','liftover_version'],
    'cs3.project':['transform_result','source_coverage','sequence_contract','fasta','fai','sequence_sizes','source_scope','contigs'],
    'cs3.candidates':['projection_result','source_mode','source_provenance','source_scope','pair_semantics','labels'],
    'cs2.qc_compare':['registry','provenance_package','fasta','fai','atlas','genome_receipt','atlas_receipt','contigs','compared_key'],
}


def saved_preview_display(preview: dict) -> dict:
    """Render saved facts, never mutable form values; verify optional receipt bytes."""
    facts=json.loads(json.dumps(dict(scope=preview['scope'],parameters=preview['parameters']),allow_nan=False))
    provenance=facts['parameters'].get('selection_provenance',{})
    receipt=provenance.get('receipt')
    if receipt:
        path=Path(receipt['path']);before=path.stat()
        frame=lambda st:[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
        if before.st_size>2*2**20 or before.st_size!=receipt['bytes'] or ('stat' in receipt and frame(before)!=receipt['stat']):
            raise ValueError('Saved selection receipt changed after preview')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt['sha256'] or frame(before)!=frame(path.stat()):
            raise ValueError('Saved selection receipt changed after preview')
        document=json.loads(raw)
        if document.get('schema')!='scpolya.gui.cs1.selection_preparation.v1':raise ValueError('Unsupported saved selection receipt')
        facts['pinned_selection_details']={key:document[key] for key in ('selection_rule','excluded_unit_ids','excluded_selection_reason') if key in document}
    return facts


def render_analyses_page(state: ProductAppState):
    registries=state.registries
    records=[r for r in registries.run_records.records if r.workspace_id==state.selected_workspace_id and r.status=='succeeded'] if registries else []
    workspace=get_workspace(registries.workspaces,state.selected_workspace_id) if registries else None
    mappings=tuple(workspace.path_mappings) if workspace else ()
    methods=[m for m in service.capabilities() if m['id'] in FIELDS]
    ps:dict[str,Any]=dict(method=methods[0]['id'] if methods else '',run_a=state.payload.get('selected_run_record_id') or '',run_b='',
                          values={'_workspace_id':state.selected_workspace_id},preview=None,target=None,index=None,rows=[],table='',page=1,busy=False,error='',recent={},json_artifacts=[],json_view=None,json_error='',json_busy=False,json_generation=0)

    def field_label(name):return state.t('analysis.field.'+name)
    def method_label(name):return state.t('analysis.method.'+name)
    def fail(exc):
        ps['error']=str(exc);preview_panel.refresh()
    def invalidate():
        ps['preview']=None;ps['error']='';preview_panel.refresh()
    def set_value(name,value):
        ps['values'][name]=value or '';invalidate()
    def choose_run(name,value):
        ps[name]=value or '';ps['target']=None;ps['index']=None;ps['rows']=[];ps['load_generation']=ps.get('load_generation',0)+1;invalidate();result_panel.refresh()
    def choose_method(value):
        ps.update(method=value,preview=None,target=None,index=None,rows=[],error='');form_panel.refresh();preview_panel.refresh();result_panel.refresh()

    async def preview():
        if ps['busy']:return
        chosen=[] if ps['method']=='cs3.transform' else [ps['run_a']]+([ps['run_b']] if ps['method']=='cs2.qc_compare' else [])
        selected=[next((r for r in records if r.id==identity),None) for identity in chosen]
        if any(r is None for r in selected):fail(ValueError(state.t('analysis.choose_run')));return
        identity=(ps['method'],chosen,dict(ps['values']))
        ps.update(busy=True,error='',preview=None);preview_panel.refresh()
        try:
            value=await run.io_bound(service.preview_selection,ps['method'],selected,mappings,dict(ps['values']))
            if identity==(ps['method'],[] if ps['method']=='cs3.transform' else [ps['run_a']]+([ps['run_b']] if ps['method']=='cs2.qc_compare' else []),ps['values']):ps['preview']=value
        except Exception as exc:fail(exc)
        finally:ps['busy']=False;preview_panel.refresh()

    def choose_recovery(value):
        ps['recovery_source']=value;invalidate();result_panel.refresh()

    async def recovery_preview():
        if ps['busy'] or not ps.get('recovery_source'):return
        selected=ps['recovery_source']
        ps.update(busy=True,error='',preview=None);preview_panel.refresh()
        try:
            value=await run.io_bound(recovery.preview,selected)
            if ps.get('recovery_source')==selected:ps['preview']=value
        except Exception as exc:fail(exc)
        finally:ps['busy']=False;preview_panel.refresh()

    async def start(value):
        try:
            ps.update(busy=True,error='');preview_panel.refresh()
            target=await run.io_bound(service.start,value)
            # A successful launch changes the selected task. Drop the previous
            # task's result and invalidate its in-flight readers before polling.
            ps.update(target=target,index=None,rows=[],table='',page=1,result_recovery=None,
                      loading_result=None,load_generation=ps.get('load_generation',0)+1,
                      json_artifacts=[],json_view=None,json_name='',json_error='',json_busy=False,cs3_details=False,
                      json_generation=ps['json_generation']+1)
            ps['preview']=None;await refresh_tasks();result_panel.refresh()
        except Exception as exc:fail(exc)
        finally:ps['busy']=False;preview_panel.refresh()

    async def load_result(target):
        target=Path(target)
        ps.update(target=target,index=None,rows=[],page=1,json_artifacts=[],json_view=None,json_name='',json_error='',json_busy=False,cs3_details=False,json_generation=ps['json_generation']+1)
        generation=ps.get('load_generation',0)+1;ps['load_generation']=generation;ps['loading_result']=target
        result_panel.refresh()
        try:
            if service.status(target)['status']=='succeeded':
                saved=json.loads((target/'preview.json').read_text())
                cs3_details=saved.get('operation')=='export_only' and saved.get('method')=='cs3.candidates'
                index=await run.io_bound(details.read_result if cs3_details else service.read_result,target)
                json_artifacts=(await run.io_bound(details.artifacts,target,index) if cs3_details else
                                await run.io_bound(json_service.artifacts,target,index) if index.get('method')=='cs2.qc_compare' else [])
                tables=index.get('tables',[])
                reader=details.table_page if cs3_details else service.table_page
                rows=await run.io_bound(reader,target,tables[0],1) if tables else []
                if ps['target']!=target or ps['load_generation']!=generation:return
                ps.update(index=index,result_runs=saved.get('selected_runs',[]),result_recovery=saved if saved.get('operation')=='export_only' else None,table=tables[0]['path'] if tables else '',rows=rows,json_artifacts=json_artifacts,cs3_details=cs3_details)
        except Exception as exc:
            if ps['target']==target and ps['load_generation']==generation:fail(exc)
        finally:
            if ps.get('loading_result')==target and ps.get('load_generation')==generation:ps['loading_result']=None
        result_panel.refresh()

    async def load_table(name,page=1):
        target=ps['target'];index=ps['index'];generation=ps.get('load_generation',0)
        try:
            meta=next(t for t in index['tables'] if t['path']==name)
            reader=details.table_page if ps.get('cs3_details') and meta['path'] in details.JSONL|{'data/candidates.tsv','data/selection-audit.jsonl'} else service.table_page
            if ps.get('cs3_details') and reader is service.table_page:await run.io_bound(details.read_result,target)
            rows=await run.io_bound(reader,target,meta,page)
            if ps.get('cs3_details') and reader is service.table_page:await run.io_bound(details.read_result,target)
            if target!=ps['target'] or index is not ps['index'] or generation!=ps.get('load_generation',0):return
            ps.update(table=name,page=page,rows=rows);result_panel.refresh()
        except Exception as exc:
            if target==ps['target'] and index is ps['index'] and generation==ps.get('load_generation',0):fail(exc)

    async def download():
        target=ps['target'];generation=ps.get('load_generation',0)
        try:
            path=await run.io_bound(service.download_path,target)
            if target!=ps['target'] or generation!=ps.get('load_generation',0):return
            ui.download.file(str(path),filename=target.name+'-report.zip')
        except Exception as exc:
            if target==ps['target'] and generation==ps.get('load_generation',0):fail(exc)

    async def load_json(name,pointer='',page=1):
        target=ps['target'];generation=ps['json_generation']+1
        ps.update(json_generation=generation,json_busy=True,json_error='',json_view=None,json_name=name)
        json_panel.refresh()
        try:
            artifact=next(item for item in ps['json_artifacts'] if item['path']==name)
            value=await run.io_bound(details.node_page if ps.get('cs3_details') else json_service.node_page,target,artifact,pointer,page)
            if target==ps['target'] and generation==ps['json_generation']:ps['json_view']=value
        except Exception as exc:
            if target==ps['target'] and generation==ps['json_generation']:ps['json_error']=str(exc)
        finally:
            if target==ps['target'] and generation==ps['json_generation']:
                ps['json_busy']=False;json_panel.refresh()

    async def download_json():
        target=ps['target'];name=ps.get('json_name');generation=ps.get('load_generation',0)
        try:
            artifact=next(item for item in ps['json_artifacts'] if item['path']==name)
            # Enforce artifact/index/export membership again before the existing
            # seal-checked download path; no arbitrary output path is accepted.
            if ps.get('cs3_details'):
                if name!=details.SHORTLIST:raise ValueError('Use the complete report for original JSONL files')
                raw=await run.io_bound(details.download_shortlist,target)
            else:
                await run.io_bound(json_service.document,target,artifact)
                raw=await run.io_bound(service.download_bytes,target,name)
            if target!=ps['target'] or generation!=ps.get('load_generation',0) or name!=ps.get('json_name'):return
            ui.download(raw,filename=Path(name).name)
        except Exception as exc:
            if target==ps['target'] and generation==ps.get('load_generation',0) and name==ps.get('json_name'):
                ps['json_error']=str(exc);json_panel.refresh()

    @ui.refreshable
    def json_panel():
        artifacts=ps['json_artifacts']
        if not artifacts:return
        ui.separator()
        ui.label(state.t('analysis.details_title' if ps.get('cs3_details') else 'analysis.json_title')).classes('ov-run-name')
        ui.label(state.t('analysis.details_help' if ps.get('cs3_details') else 'analysis.json_help')).classes('product-small-muted whitespace-pre-line')
        if ps.get('cs3_details'):
            ui.button(state.t('analysis.details_shortlist'),on_click=lambda:load_json(details.SHORTLIST,'/rows')).props('outline no-caps')
        ui.select({item['path']:item['path'] for item in artifacts},with_input=True,value=ps.get('json_name') if ps.get('json_name') in {item['path'] for item in artifacts} else None,
                  label=state.t('analysis.json_artifact'),on_change=lambda e:load_json(e.value)).props('dense outlined options-dense').classes('w-full')
        if ps['json_busy']:
            with ui.row().classes('items-center gap-2'):
                ui.spinner(size='sm');ui.label(state.t('analysis.verifying')).classes('product-small-muted')
        if ps['json_error']:
            ui.label(state.t('analysis.error')).classes('ov-alert ov-alert-info w-full')
            with ui.expansion(state.t('evidence.error_details'),icon='o_info').classes('w-full'):
                ui.label(ps['json_error']).classes('product-small-muted break-all')
        value=ps['json_view']
        if not value:return
        name=value['source']['path'];pointer=value['pointer']
        if value.get('record_ordinal') is not None:
            _,separator,tail=pointer[1:].partition('/')
            ui.label(state.t('analysis.details_line',source=name,line=value['record_ordinal']+1,pointer='/'+tail if separator else '')).classes('product-small-muted break-all')
        else:ui.label(name+'#'+pointer).classes('product-small-muted break-all')
        if ps.get('cs3_details'):
            ui.label(state.t('analysis.details_origin',task=value['recovery_task'],source=value['source_task'])).classes('product-small-muted break-all')
        with ui.expansion(state.t('analysis.json_source'),icon='o_description').classes('w-full'):
            ui.code(json.dumps(value['source'],ensure_ascii=False,indent=2),language='json').classes('w-full max-h-48 overflow-auto')
        with ui.row().classes('w-full items-center gap-2'):
            ui.button(state.t('analysis.json_root'),on_click=lambda:load_json(name)).props('flat no-caps')
            parent=ui.button(state.t('analysis.json_parent'),on_click=lambda:load_json(name,pointer.rsplit('/',1)[0])).props('flat no-caps');parent.set_enabled(bool(pointer))
            if not ps.get('cs3_details') or name==details.SHORTLIST:
                ui.button(state.t('analysis.json_download'),icon='o_download',on_click=download_json).props('outline no-caps')
        if name=='masking/transitions.json':ui.label(state.t('analysis.json_transition_note')).classes('product-small-muted')
        if name=='descriptive/composition-standardization.json':ui.label(state.t('analysis.json_composition_note')).classes('product-small-muted')
        def cell(item):
            if 'literal' in item:return item['literal']
            label=state.t('analysis.json_kind.'+item['kind'])
            return label+(f' ({item["count"]})' if item.get('count') is not None else '')+' · '+state.t('analysis.json_open')
        if value['ancestors']:
            with ui.expansion(state.t('analysis.json_context'),icon='o_description').classes('w-full'):
                ui.label(state.t('analysis.json_context_help')).classes('product-small-muted')
                contexts=[dict(source_pointer=a['pointer'],context_fields=a['context_fields'],fields={key:json.loads(item['literal']) if 'literal' in item else cell(item) for key,item in a['fields'].items()}) for a in value['ancestors']]
                ui.code(json.dumps(contexts,ensure_ascii=False,indent=2),language='json').classes('w-full max-h-64 overflow-auto')
        columns=[dict(name='entry',label=state.t('analysis.json_entry'),field='entry',align='left')]
        fields=value['fields']
        if fields:columns += [dict(name='field_'+str(i),label=key,field='field_'+str(i),align='left') for i,key in enumerate(fields)]
        else:columns += [dict(name='kind',label=state.t('analysis.json_type'),field='kind',align='left'),dict(name='value',label=state.t('analysis.json_value'),field='value',align='left')]
        columns.append(dict(name='open',label='',field='open',align='left'))
        rows=[]
        for item in value['rows']:
            row=dict(entry=item['key'],kind=state.t('analysis.json_kind.'+item['kind']),value=cell(item['preview']),__pointer=item['pointer'])
            for i,key in enumerate(fields):row['field_'+str(i)]=cell(item['values'][key]) if key in item['values'] else state.t('analysis.json_missing')
            rows.append(row)
        table=ui.table(columns=columns,rows=rows,row_key='__pointer').classes('analysis-result-table analysis-json-table')
        table.style('max-height:420px;overflow:auto;')
        table.add_slot('header','<q-tr :props="props"><q-th v-for="col in props.cols" :key="col.name" :props="props" style="position:sticky;top:0;z-index:1;background:var(--sp-surface)">{{ col.label }}</q-th></q-tr>')
        from html import escape
        table.add_slot('body-cell-open','<q-td :props="props"><q-btn dense flat no-caps label="'+escape(state.t('analysis.json_open'),quote=True)+'" @click="$parent.$emit(\'open-json-node\', props.row.__pointer)" /></q-td>')
        table.on('open-json-node',lambda e:load_json(name,e.args))
        with ui.row().classes('w-full items-center justify-end gap-2'):
            unit=state.t('analysis.json_unit.'+value['unit'])
            ui.label(state.t('analysis.json_page',page=value['page'],pages=value['pages'],total=value['total'],unit=unit)).classes('product-small-muted')
            first=ui.button(state.t('analysis.json_first'),on_click=lambda:load_json(name,pointer,1)).props('flat no-caps');first.set_enabled(value['page']>1)
            previous=ui.button(state.t('analysis.json_previous'),on_click=lambda:load_json(name,pointer,value['page']-1)).props('flat no-caps');previous.set_enabled(value['page']>1)
            following=ui.button(state.t('analysis.json_next'),on_click=lambda:load_json(name,pointer,value['page']+1)).props('flat no-caps');following.set_enabled(value['page']<value['pages'])
            last=ui.button(state.t('analysis.json_last'),on_click=lambda:load_json(name,pointer,value['pages'])).props('flat no-caps');last.set_enabled(value['page']<value['pages'])

    async def refresh_tasks():
        recent={}
        for target in service.recent():
            try:
                saved=json.loads((target/'preview.json').read_text())
                if saved.get('workspace_id')==state.selected_workspace_id:
                    recent[str(target)]=method_label(saved['method'])+' · '+target.name+(' · '+state.t('analysis.recovery.label') if saved.get('operation')=='export_only' else '')
            except (OSError,ValueError,KeyError):pass
        changed=ps.get('recent')!=recent
        ps['recent']=recent
        if ps['target'] and service.status(ps['target'])['status']=='succeeded' and ps['index'] is None and ps.get('loading_result')!=ps['target']:
            await load_result(ps['target'])
            return
        stamp=json.dumps(service.status(ps['target']),sort_keys=True) if ps['target'] else ''
        if changed or stamp!=ps.get('status_stamp'):
            ps['status_stamp']=stamp
            result_panel.refresh()

    @ui.refreshable
    def form_panel():
        if not methods:
            ui.label(state.t('analysis.no_methods')).classes('env-note');return
        ui.select({m['id']:method_label(m['id']) for m in methods},value=ps['method'],label=state.t('analysis.method'),
                  on_change=lambda e:choose_method(e.value)).props('dense outlined options-dense').classes('w-full')
        selected=next(m for m in methods if m['id']==ps['method'])
        ui.label(state.t('analysis.description.'+ps['method'])).classes('product-small-muted')
        options={r.id:f'{r.label} · {(r.invocation_id or r.job_id or r.id)[:12]}' for r in records}
        with ui.row().classes('w-full items-center gap-3'):
            ui.select(options,value=ps['run_a'] if ps['run_a'] in options else None,label=state.t('analysis.run_a'),
                      on_change=lambda e:choose_run('run_a',e.value)).props('dense outlined options-dense').classes('flex-1 min-w-64').set_visibility(ps['method']!='cs3.transform')
            if ps['method']=='cs3.transform':
                pass
            if ps['method']=='cs2.qc_compare':
                ui.select(options,value=ps['run_b'] if ps['run_b'] in options else None,label=state.t('analysis.run_b'),
                          on_change=lambda e:choose_run('run_b',e.value)).props('dense outlined options-dense').classes('flex-1 min-w-64')
        with ui.element('div').classes('analysis-form-grid'):
            for name in FIELDS[ps['method']]:
                default='unscaled_depth_threshold_polyA' if name=='compared_key' else ''
                ps['values'].setdefault(name,default)
                choices={'source_coordinate_base':['0based','1based'],'coordinate_status':['documented','operational_inference','user_declared'],'source_format':['polyadb32','normalized_points'],'source_mode':['native_snapshot','historical_export','synthetic'],'pair_semantics':['unknown','unique_pairs_required']}
                if name in choices:
                    options={value:state.t('analysis.choice.'+value) for value in choices[name]}
                    ui.select(options,label=field_label(name),value=ps['values'][name] or None,on_change=lambda e,name=name:set_value(name,e.value)).props('dense outlined').classes('w-full min-w-0')
                else:
                    ui.input(field_label(name),value=ps['values'][name],on_change=lambda e,name=name:set_value(name,e.value)).props('dense outlined').classes('w-full min-w-0')
        if ps['method'].startswith('cs3.'):
            ui.label(state.t('analysis.cs3_contract')).classes('product-small-muted')
        if ps['method']=='cs2.qc_compare':ui.label(state.t('analysis.runtime_note')).classes('product-small-muted')
        with ui.expansion(state.t('analysis.method_details'),icon='o_info').classes('w-full'):
            ui.code(json.dumps(selected['parameters'],ensure_ascii=False,indent=2),language='json').classes('w-full max-h-64 overflow-auto')
        ui.button(state.t('analysis.preview'),icon='o_manage_search',on_click=preview).props('outline no-caps')

    @ui.refreshable
    def preview_panel():
        if ps['error']:
            ui.label(state.t('analysis.error')).classes('ov-alert ov-alert-info w-full')
            with ui.expansion(state.t('evidence.error_details'),icon='o_info').classes('w-full'):
                ui.label(ps['error']).classes('product-small-muted break-all')
        if ps['busy']:
            with ui.row().classes('items-center gap-2'):
                ui.spinner(size='sm');ui.label(state.t('analysis.verifying')).classes('product-small-muted')
        value=ps['preview']
        if not value:return
        try:
            facts=saved_preview_display(value)
        except Exception as exc:
            ui.label(state.t('evidence.error.source_changed')).classes('ov-alert ov-alert-info w-full')
            with ui.expansion(state.t('evidence.error_details'),icon='o_info').classes('w-full'):
                ui.label(str(exc)).classes('product-small-muted break-all')
            return
        with ui.column().classes('ov-alert ov-alert-info w-full gap-2'):
            ui.label(state.t('analysis.recovery.preview_title' if value.get('operation')=='export_only' else 'analysis.preview_title')).classes('ov-alert-title')
            resources=value['resources']
            ui.label(state.t('analysis.budget',cpu=resources['cpu_cores'],memory=resources['memory_bytes']//2**30,seconds=resources['wall_seconds'])).classes('product-small-muted')
            ui.label(state.t('analysis.fixed_scope')).classes('product-small-muted')
            scope=facts['scope']
            if all(key in scope for key in ('cohort_cells','selected_units','registry_cells')):
                ui.label(state.t('analysis.preview_counts',cells=scope['cohort_cells'],units=scope['selected_units'],registry=scope['registry_cells'])).classes('ov-alert-title')
            if scope.get('group_counts'):
                ui.label(state.t('analysis.preview_groups')+': '+', '.join(f'{name}: {count}' for name,count in scope['group_counts'].items())).classes('product-small-muted')
            selection=facts['parameters'].get('selection_provenance',{})
            if selection:
                ui.label(state.t('analysis.preview_selection')+': '+str(selection.get('mode',''))).classes('product-small-muted')
                if selection.get('limitation'):ui.label(selection['limitation']).classes('product-small-muted whitespace-pre-line')
                if selection.get('excluded_unit_ids'):
                    ui.label(state.t('analysis.preview_excluded')+': '+', '.join(map(str,selection['excluded_unit_ids']))).classes('product-small-muted break-all')
                reason=facts.get('pinned_selection_details',{}).get('excluded_selection_reason')
                if reason:ui.label(reason).classes('product-small-muted whitespace-pre-line')
            with ui.expansion(state.t('analysis.scope_sources'),icon='o_description').classes('w-full'):
                ui.code(json.dumps(dict(**facts,sources=value['backend_plan'].get('input_sources',value['backend_plan'].get('binding',{}))),ensure_ascii=False,indent=2),language='json').classes('w-full max-h-80 overflow-auto')
            def confirm():
                with ui.dialog() as dialog,ui.card().classes('w-[520px] max-w-full'):
                    ui.label(state.t('analysis.recovery.confirm_title' if value.get('operation')=='export_only' else 'analysis.confirm_title')).classes('text-lg font-semibold')
                    ui.label(state.t('analysis.recovery.confirm_body' if value.get('operation')=='export_only' else 'analysis.confirm_body')).classes('product-small-muted')
                    with ui.row().classes('w-full justify-end'):
                        ui.button(state.t('common.cancel'),on_click=dialog.close).props('flat no-caps')
                        async def submit():dialog.close();await start(value)
                        ui.button(state.t('analysis.recovery.run' if value.get('operation')=='export_only' else 'analysis.run'),on_click=submit).props('no-caps')
                dialog.open()
            ui.button(state.t('analysis.recovery.run' if value.get('operation')=='export_only' else 'analysis.run'),icon='o_play_arrow',on_click=confirm).props('no-caps')

    @ui.refreshable
    def result_panel():
        choices=recovery.sources(state.selected_workspace_id)
        if choices:
            ui.label(state.t('analysis.recovery.title')).classes('ov-run-name')
            ui.label(state.t('analysis.recovery.description')).classes('product-small-muted')
            ui.select(choices,value=ps.get('recovery_source'),label=state.t('analysis.recovery.source'),
                      on_change=lambda e:choose_recovery(e.value)).props('dense outlined options-dense').classes('w-full')
            ui.button(state.t('analysis.recovery.verify'),icon='o_fact_check',on_click=recovery_preview).props('outline no-caps')
            if ps.get('recovery_source'):
                with ui.expansion(state.t('analysis.recovery.history'),icon='o_history').classes('w-full'):
                    source=recovery.source_task(ps['recovery_source'])
                    ui.label(source.name+' · FAILED').classes('sc-scorer-chip')
                    ui.label(str(source)).classes('product-small-muted break-all')
                    log=source/'worker.log'
                    if log.is_file() and not log.is_symlink():
                        with log.open('rb') as handle:handle.seek(max(0,log.stat().st_size-32768));raw=handle.read(32768)
                        ui.code(raw.decode('utf-8',errors='replace'),language='text').classes('w-full h-64 overflow-auto')
        ui.label(state.t('analysis.saved_tasks')).classes('ov-run-name')
        if ps['recent']:
            ui.select(ps['recent'],value=str(ps['target']) if ps['target'] else None,label=state.t('analysis.task'),on_change=lambda e:load_result(e.value)).props('dense outlined options-dense').classes('w-full')
        if not ps['target']:return
        current=service.status(ps['target'])
        ui.label(state.t('analysis.status.'+current['status'])).classes('sc-scorer-chip')
        if current['status'] in ('starting','running','computation_complete'):
            ui.linear_progress().props('indeterminate')
        if current['status']=='failed':
            ui.label(state.t('analysis.recovery.failure') if current.get('error')==recovery.LEGACY_ERROR else state.t('analysis.error')).classes('product-small-muted break-all')
            with ui.expansion(state.t('evidence.error_details'),icon='o_info').classes('w-full'):
                ui.label(current.get('error','')).classes('product-small-muted break-all')
        log=ps['target']/'worker.log'
        if log.is_file():
            with ui.expansion(state.t('analysis.logs'),icon='o_terminal').classes('w-full'):
                # Bounded tail only; long logs stay in their own pane.
                with log.open('rb') as handle:handle.seek(max(0,log.stat().st_size-32768));raw=handle.read(32768)
                ui.code(raw.decode('utf-8',errors='replace'),language='text').classes('w-full h-64 overflow-auto')
        index=ps['index']
        if ps.get('loading_result')==ps['target']:
            with ui.row().classes('items-center gap-2'):
                ui.spinner(size='sm');ui.label(state.t('analysis.verifying')).classes('product-small-muted')
        if not index:return
        if ps.get('result_recovery'):
            value=ps['result_recovery'];binding=value['backend_plan']['binding']
            ui.label(state.t('analysis.recovery.succeeded')).classes('ov-alert ov-alert-info w-full')
            ui.label(state.t('analysis.recovery.origin',task=Path(binding['source_task']).name)).classes('product-small-muted break-all')
        ui.button(state.t('analysis.export'),icon='o_download',on_click=download).props('outline no-caps')
        with ui.expansion(state.t('analysis.summary'),icon='o_description').classes('w-full'):
            ui.code(json.dumps(index.get('summary',{}),ensure_ascii=False,indent=2),language='json').classes('w-full max-h-80 overflow-auto')
            ui.label('\n'.join(index.get('limitations',[]))).classes('product-small-muted whitespace-pre-line')
        tables=index.get('tables',[])
        if tables:
            ui.select({t['path']:t['name'] for t in tables},value=ps['table'],label=state.t('analysis.result_table'),on_change=lambda e:load_table(e.value)).props('dense outlined').classes('w-full')
            meta=next(t for t in tables if t['path']==ps['table'])
            is_jsonl=ps.get('cs3_details') and meta.get('format')=='jsonl'
            columns=list(dict.fromkeys(key for row in ps['rows'] for key in row)) if is_jsonl else list(ps['rows'][0]) if ps['rows'] else meta.get('columns',[])
            def display(value):
                if isinstance(value,(dict,list)):
                    return state.t('analysis.json_kind.'+json_service.kind(value))+f' ({len(value)})'
                item=json_service.preview(value)
                return item.get('literal',state.t('analysis.json_kind.'+item['kind'])+f' ({item.get("count",0)})')
            rows=[dict({key:display(row[key]) if key in row else state.t('analysis.json_missing') for key in columns},__row=str(i),__record=(ps['page']-1)*25+i) if is_jsonl else dict(row,**{'__row':str(i)}) for i,row in enumerate(ps['rows'])]
            row_details=ps.get('cs3_details') and meta['path'] in details.JSONL
            defs=[dict(name=c,label=c,field=c,align='left') for c in columns]
            if row_details:defs.insert(0,dict(name='details',label='',field='details',align='left'))
            table=ui.table(columns=defs,rows=rows,row_key='__row').classes('analysis-result-table')
            if row_details:
                target=ps['target'];generation=ps.get('load_generation',0);name=meta['path']
                async def open_record(event,target=target,generation=generation,name=name):
                    if target!=ps['target'] or generation!=ps.get('load_generation',0) or name!=ps['table']:return
                    await load_json(name,'/'+str(event.args))
                from html import escape
                table.add_slot('body-cell-details','<q-td :props="props"><q-btn dense flat no-caps label="'+escape(state.t('analysis.details_row'),quote=True)+'" @click="$parent.$emit(\'open-record\', props.row.__record)" /></q-td>')
                table.on('open-record',open_record)
            table.style('max-height:420px;overflow:auto;')
            table.add_slot('header', '<q-tr :props="props"><q-th v-for="col in props.cols" :key="col.name" :props="props" style="position:sticky;top:0;z-index:1;background:var(--sp-surface)">{{ col.label }}</q-th></q-tr>')
            with ui.row().classes('w-full items-center justify-end gap-2'):
                ui.label(state.t('analysis.page',page=ps['page'],total=meta.get('row_count','?'))).classes('product-small-muted')
                previous=ui.button(icon='o_chevron_left',on_click=lambda:load_table(ps['table'],max(1,ps['page']-1))).props('flat dense')
                previous.set_enabled(ps['page']>1)
                next_button=ui.button(icon='o_chevron_right',on_click=lambda:load_table(ps['table'],ps['page']+1)).props('flat dense')
                next_button.set_enabled(ps['page']*25<meta['row_count'] if row_details else len(ps['rows'])==25)
            if ps['rows']:
                with ui.expansion(state.t('analysis.genome_links'),icon='o_ssid_chart').classes('w-full'):
                    for row in ps['rows']:
                        gene=row.get('gene_id')
                        if not gene and row.get('terminal_gene_unit_id'):
                            from urllib.parse import unquote
                            gene=unquote(row['terminal_gene_unit_id'].rsplit('|',1)[0])
                        sources=ps.get('result_runs',[])
                        if gene and sources:
                            ui.link(gene,state.page_href('igv_compare',extra_query={'igv_source':'single','run_record':sources[0]['record_id'],'gene':gene}))
        json_panel()

    with ui.column().classes('env-page sc-page w-full gap-4'):
        ui.label(state.t('analysis.title')).classes('ov-title')
        ui.label(state.t('analysis.intro')).classes('product-small-muted')
        form_panel();preview_panel();result_panel()
        ui.timer(1,refresh_tasks)
