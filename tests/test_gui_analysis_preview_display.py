"""Preview presentation uses persisted facts and byte-verified source metadata."""
from pathlib import Path
import hashlib,json
import pytest
from scpolya_ui.pages.analyses import saved_preview_display


def test_saved_scope_and_parameters_are_not_mutable_form_values(tmp_path):
    receipt=tmp_path/'selection.json'
    receipt.write_text(json.dumps(dict(schema='scpolya.gui.cs1.selection_preparation.v1',selection_rule='old shared-support subset',excluded_unit_ids=['G-excluded|+'],excluded_selection_reason='Group1 pooled t=0')))
    pin=dict(path=str(receipt),bytes=receipt.stat().st_size,sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
    saved=dict(scope=dict(cohort_cells=7,selected_units=3,registry_cells=9,group_counts=dict(Group1=4,Group2=3)),parameters=dict(selection_provenance=dict(mode='pinned_selection_receipt',excluded_unit_ids=['G-excluded|+'],receipt=pin)))
    mutable_form=dict(cells=999,units=888,groups='unrelated')
    shown=saved_preview_display(saved)
    mutable_form.update(cells=1000)
    saved['scope']['cohort_cells']=2000
    assert shown['scope']['cohort_cells']==7 and shown['scope']['selected_units']==3
    assert shown['parameters']['selection_provenance']['mode']=='pinned_selection_receipt'
    assert shown['pinned_selection_details']['excluded_selection_reason']=='Group1 pooled t=0'
    receipt.write_text('changed')
    with pytest.raises(ValueError,match='changed after preview'):saved_preview_display(saved)


def test_other_methods_show_saved_parameters_without_selection_receipt():
    value=saved_preview_display(dict(scope=dict(contigs=['chr1']),parameters=dict(cutoffs=[3,5,10])))
    assert value==dict(scope=dict(contigs=['chr1']),parameters=dict(cutoffs=[3,5,10]))
