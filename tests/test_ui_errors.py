import json
from pathlib import Path

import pytest

from scpolya_ui.errors import BACKEND_PATTERNS, UserError, error_text
from scpolya_ui.i18n.catalog import translate
from scpolya_ui.registry import WorkspaceRegistry
from scpolya_ui.services.workspace_service import register_workspace

LOCALES = Path(__file__).resolve().parents[1] / "scpolya_ui" / "i18n" / "locales"


def zh(key, **params):
    return translate("zh-CN", key, **params)


def test_user_error_keeps_english_message_and_translates():
    error = UserError("error.diff.outdir_unwritable", "Cannot create the output directory under /x", parent="/x")
    assert isinstance(error, ValueError)
    assert str(error) == "Cannot create the output directory under /x"
    assert error_text(zh, error) == "无法在 /x 下创建输出目录。"


def test_backend_messages_translate_with_or_without_exception_prefix():
    assert error_text(zh, "ValueError: Manifest must contain 1..128 libraries") == "样本清单里的文库数必须在 1 到 128 之间。"
    assert error_text(zh, ValueError("Input file missing: /a/b.tsv")) == "找不到输入文件：/a/b.tsv"
    assert error_text(zh, KeyError("Unknown sweep parameter: bogus")) == "bogus 不是可以扫描的参数。"


def test_unknown_messages_pass_through_unchanged():
    assert error_text(zh, ValueError("something new")) == "something new"


def test_every_backend_pattern_has_catalog_entries_in_all_locales():
    catalogs = [json.loads(path.read_text(encoding="utf-8")) for path in LOCALES.glob("*.json")]
    for _pattern, key in BACKEND_PATTERNS:
        for catalog in catalogs:
            assert key in catalog, key


@pytest.mark.parametrize("path", ["", "relative/results"])
def test_register_workspace_requires_absolute_path(tmp_path, path):
    with pytest.raises(UserError) as caught:
        register_workspace(WorkspaceRegistry(), path=path, home=tmp_path)
    assert caught.value.key == "error.workspace.path_absolute"


def test_register_workspace_rejects_missing_path(tmp_path):
    with pytest.raises(UserError) as caught:
        register_workspace(WorkspaceRegistry(), path=tmp_path / "absent", home=tmp_path)
    assert caught.value.key == "error.workspace.path_missing"
