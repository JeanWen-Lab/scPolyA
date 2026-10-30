"""GUI pre-launch notice for run roots that record another annotation schema."""
from __future__ import annotations

from types import SimpleNamespace

from scpolya_ui.i18n.catalog import load_catalog
from scpolya_ui.services.config_service import annotation_schema_root_notice
from scpolya_ui.services.study_service import study_annotation_schema_notices
from test_recorded_annotation_schema import V3, V4, _config, _manifest_bytes, _write_run_outputs


def test_run_setup_notice_appears_for_the_v3_root_and_clears_for_a_new_root(tmp_path):
    config = _config(tmp_path)
    assert annotation_schema_root_notice(config) == ""
    _write_run_outputs(tmp_path, config, V3, "0.25")
    before = _manifest_bytes(tmp_path)
    notice = annotation_schema_root_notice(config)
    assert V3 in notice and V4 in notice and "new run root" in notice
    assert annotation_schema_root_notice(dict(config, run_id="native-v4")) == ""
    assert annotation_schema_root_notice(dict(config, output_dir=str(tmp_path / "other"))) == ""
    assert annotation_schema_root_notice(dict(config, output_dir="")) == ""
    assert annotation_schema_root_notice(dict(config, quantification_mode="legacy")) == ""
    assert _manifest_bytes(tmp_path) == before
    assert not (tmp_path / "native-v4").exists()


def test_study_confirmation_lists_each_distinct_variant_notice():
    def variant(notice):
        return SimpleNamespace(rerun_preview=SimpleNamespace(annotation_schema_notice=notice))

    bundle = SimpleNamespace(preview=SimpleNamespace(variants=(variant("A"), variant(""), variant("A"), variant("B"))))
    assert study_annotation_schema_notices(bundle) == ("A", "B")


def test_notice_strings_exist_in_every_locale():
    for locale in ("en", "zh-CN"):
        catalog = load_catalog(locale)
        for key in ("run_setup.annotation_schema_title", "run_setup.annotation_schema_new_root"):
            assert catalog[key].strip()
