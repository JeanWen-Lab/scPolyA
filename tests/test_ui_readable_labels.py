from functools import partial

from scpolya_ui.components.config_panel import _inspector_value_text
from scpolya_ui.components.status import artifact_label
from scpolya_ui.i18n.catalog import translate
from scpolya_ui.services.differential_service import readable_value, sort_key


def test_readable_value_decodes_units_and_rounds_floats():
    assert readable_value("unit_id", "gene%3AAB%7C1") == "gene:AB|1"
    assert readable_value("logFC", "0.123456789") == "0.1235"
    assert readable_value("PValue", "1.23456e-08") == "1.235e-08"
    assert readable_value("n", "42") == "42"
    assert readable_value("FDR", "NA") == "NA"
    assert readable_value("gene", "TNFRSF1B") == "TNFRSF1B"


def test_sort_key_orders_numbers_then_text_then_missing():
    values = ["NA", "b", "0.5", "", "A", "-1"]
    assert sorted(values, key=sort_key) == ["-1", "0.5", "A", "b", "NA", ""]


def test_artifact_templates_translate_parts():
    zh = partial(translate, "zh-CN")
    en = partial(translate, "en")
    label = "Explicit per-unit plus compatibility BAMs and indexes"
    assert artifact_label(zh, label) != label
    assert "plus" not in artifact_label(zh, label)
    assert artifact_label(en, "Reference: genes.gtf").endswith("genes.gtf")
    assert artifact_label(zh, "something the pipeline added later") == "something the pipeline added later"


def test_inspector_values_use_words_not_raw_booleans():
    zh = partial(translate, "zh-CN")
    assert _inspector_value_text(True, zh) == "是"
    assert _inspector_value_text(False, zh) == "否"
    assert _inspector_value_text(None, zh) == "未设置"
    assert _inspector_value_text(3, zh) == "3"
    assert _inspector_value_text(True, None) == "true"


def test_integer_fields_refuse_fractions_instead_of_truncating():
    import pytest

    from scpolya_ui.config_schema import CONFIG_FIELDS, coerce_field_value
    from scpolya_ui.errors import UserError

    field = next(item for item in CONFIG_FIELDS if item.kind == "int")
    assert coerce_field_value(field, 20) == 20
    assert coerce_field_value(field, 20.0) == 20
    assert coerce_field_value(field, "7") == 7
    with pytest.raises(UserError) as caught:
        coerce_field_value(field, 20.5)
    assert caught.value.key == "config.error.integer_field"


def test_prop_text_survives_nicegui_props_parsing():
    from nicegui.props import Props

    from scpolya_ui.components.interaction import prop_text

    text = "letters, '.', \"quoted\" & back\\slash"
    assert Props.parse(f"tabindex=0 aria-label={prop_text(text)}")["aria-label"] == text


def test_reference_artifacts_translate_their_names():
    zh = partial(translate, "zh-CN")
    assert artifact_label(zh, "Reference: unfiltered standard chrom sizes") == "参考文件：标准染色体长度（未过滤）"
    assert artifact_label(zh, "Gene GTF backup") == "基因 GTF 备份"
