import pytest


@pytest.fixture(autouse=True)
def _isolated_ui_home(tmp_path_factory, monkeypatch):
    # UI services cache files (for example transcript subsets) under the UI
    # home; tests must never write into the developer's real ~/.scpolya-ui.
    monkeypatch.setenv("SCPOLYA_UI_HOME", str(tmp_path_factory.mktemp("ui-home")))
