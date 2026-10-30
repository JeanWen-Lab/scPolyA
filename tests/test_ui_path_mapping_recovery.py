"""A path mapping with '..' must be refused when saved, and an old one already on disk must not break Runs."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scpolya_ui.errors import UserError
from scpolya_ui.registry import PathMapping, add_path_mapping, apply_path_mappings_to_value, load_registry, registry_path
from scpolya_ui.run_data import discover_run_log_catalog
from scpolya_ui.services.workspace_service import (
    add_workspace_mapping,
    register_workspace,
    remove_workspace_mapping,
    workspace_mapping_summaries,
)
from scpolya_ui.state.persistence import load_product_registries
from test_ui_relocated_paths import _invocation

REPO = Path(__file__).resolve().parents[1]
HISTORICAL = Path("/historical/original-run")


def _workspace(tmp_path):
    """Isolated UI home with one workspace whose run was recorded under another root."""
    home = tmp_path / "uihome"
    root = tmp_path / "local"
    _invocation(root, HISTORICAL)
    registries = load_product_registries(home)
    workspace = register_workspace(registries.workspaces, path=str(root), alias="Moved", home=home)
    return home, root, workspace.id


@pytest.mark.parametrize("old,new", [
    ("/review-nonmatching/../source", "{tmp}"),
    ("/old/run", "{tmp}/../{tmp_name}"),
])
def test_save_refuses_parent_traversal_on_either_side_without_writing(tmp_path, old, new):
    home, _, workspace_id = _workspace(tmp_path)
    before = registry_path(home).read_bytes()
    registries = load_product_registries(home)
    new = new.format(tmp=tmp_path, tmp_name=tmp_path.name)
    with pytest.raises(UserError) as caught:
        add_workspace_mapping(registries.workspaces, workspace_id, old_path=old, new_path=new, home=home)
    assert caught.value.key == "workspace.mapping_error.traversal"
    assert registry_path(home).read_bytes() == before
    assert load_product_registries(home).workspaces.workspaces[0].path_mappings == []
    # The lower-level helper refuses with the same rule, before changing the record.
    record = registries.workspaces.workspaces[0]
    with pytest.raises(ValueError):
        add_path_mapping(record, old_path=old, new_path=new)
    assert record.path_mappings == []


def _seed_historical_mappings(home, workspace_id, root):
    """Write mappings the way an older version could have saved them."""
    path = registry_path(home)
    data = json.loads(path.read_text())
    entry = next(item for item in data["workspaces"] if item["id"] == workspace_id)
    entry["path_mappings"] = [
        {"old_path": "/review-nonmatching/../source", "new_path": "/tmp", "note": "", "created_at": ""},
        {"old_path": str(HISTORICAL), "new_path": str(root), "note": "real move", "created_at": ""},
        {"old_path": str(HISTORICAL), "new_path": "/elsewhere", "note": "", "created_at": ""},
    ]
    path.write_text(json.dumps(data))


def test_historical_bad_mappings_are_listed_not_applied_and_removable(tmp_path):
    home, root, workspace_id = _workspace(tmp_path)
    _seed_historical_mappings(home, workspace_id, root)

    registries = load_product_registries(home)  # also syncs run records through the mappings
    workspace = registries.workspaces.workspaces[0]
    assert [m.old_path for m in workspace.path_mappings] == [str(HISTORICAL)]
    assert [m.old_path for m in workspace.ignored_path_mappings] == ["/review-nonmatching/../source", str(HISTORICAL)]
    # The legitimate mapping keeps working: the moved run is found and its paths are shown where they are now.
    assert len(discover_run_log_catalog(root, path_mappings=tuple(workspace.path_mappings)).invocations) == 1
    assert [r.root_path for r in registries.run_records.records] == [str(HISTORICAL)]
    assert apply_path_mappings_to_value("/unrelated/log", workspace.path_mappings) == "/unrelated/log"
    assert apply_path_mappings_to_value(f"{HISTORICAL}/x", workspace.path_mappings) == f"{root}/x"

    rows = workspace_mapping_summaries(workspace)
    assert [(row.old_path, row.problem) for row in rows] == [
        (str(HISTORICAL), ""), ("/review-nonmatching/../source", "traversal"), (str(HISTORICAL), "duplicate"),
    ]

    # Saving anything else keeps the ignored entries on disk until they are removed explicitly.
    remove_workspace_mapping(registries.workspaces, workspace_id, old_path="/review-nonmatching/../source", home=home)
    remove_workspace_mapping(registries.workspaces, workspace_id, old_path=str(HISTORICAL), new_path="/elsewhere", home=home)
    reloaded = load_registry(home).workspaces[0]
    assert reloaded.ignored_path_mappings == []
    assert [(m.old_path, m.new_path, m.note) for m in reloaded.path_mappings] == [(str(HISTORICAL), str(root), "real move")]


def test_ignored_mappings_survive_an_unrelated_save(tmp_path):
    home, root, workspace_id = _workspace(tmp_path)
    _seed_historical_mappings(home, workspace_id, root)
    registries = load_product_registries(home)
    target = tmp_path / "refs"
    target.mkdir()
    add_workspace_mapping(registries.workspaces, workspace_id, old_path="/old/refs", new_path=str(target), home=home)
    saved = json.loads(registry_path(home).read_text())["workspaces"][0]
    assert "ignored_path_mappings" not in saved
    assert len(saved["path_mappings"]) == 4
    assert len(load_registry(home).workspaces[0].ignored_path_mappings) == 2


def test_unrelated_unsafe_mapping_no_longer_refuses_every_value():
    unsafe = [PathMapping("/review-nonmatching/../source", "/tmp")]
    assert apply_path_mappings_to_value("/unrelated/log", unsafe) == "/unrelated/log"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def test_runs_and_overview_pages_load_with_a_historical_bad_mapping(tmp_path):
    """End to end on a real server with an isolated UI home: no 500, and the page says what to do."""
    home, root, workspace_id = _workspace(tmp_path)
    registries = load_product_registries(home)
    # The save entry refuses the reviewer's mapping and leaves the registry as it was ...
    with pytest.raises(UserError):
        add_workspace_mapping(
            registries.workspaces, workspace_id, old_path="/review-nonmatching/../source", new_path="/tmp", home=home
        )
    # ... so the bad mapping can only come from an older version's file.
    _seed_historical_mappings(home, workspace_id, root)

    port = _free_port()
    env = {**os.environ, "SCPOLYA_UI_HOME": str(home)}
    server = subprocess.Popen(
        [sys.executable, "-m", "scpolya_ui", "--host", "127.0.0.1", "--port", str(port), "--no-browser"],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}/product"
        deadline = time.monotonic() + 60
        while True:
            try:
                status, _ = _get(f"{base}?locale=en")
                break
            except (urllib.error.URLError, ConnectionError):
                assert server.poll() is None, server.stderr.read().decode()
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.5)
        for page in ("runs", "runs?tab=attempts", ""):
            sep = "&" if "?" in page else "?"
            url = f"{base}/{page}{sep}locale=en&workspace={workspace_id}" if page else f"{base}?locale=en&workspace={workspace_id}"
            status, body = _get(url)
            assert status == 200, (url, body[-2000:])
            assert "2 saved path mappings are not applied" in body, url
        assert server.poll() is None
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def _seed_identical_pair(home, workspace_id, root):
    """An applied mapping and an ignored copy with the same old and new paths (review U2)."""
    path = registry_path(home)
    data = json.loads(path.read_text())
    entry = next(item for item in data["workspaces"] if item["id"] == workspace_id)
    entry["path_mappings"] = [
        {"old_path": str(HISTORICAL), "new_path": str(root), "note": "valid pair", "created_at": ""},
        {"old_path": str(HISTORICAL), "new_path": str(root), "note": "ignored identical duplicate", "created_at": ""},
    ]
    path.write_text(json.dumps(data))


def _discovered(home, root):
    registries = load_product_registries(home)
    workspace = registries.workspaces.workspaces[0]
    catalog = discover_run_log_catalog(root, path_mappings=tuple(workspace.path_mappings))
    return workspace, [i.invocation_id for i in catalog.invocations], [r.root_path for r in registries.run_records.records]


def test_removing_the_ignored_identical_copy_keeps_the_applied_mapping(tmp_path):
    home, root, workspace_id = _workspace(tmp_path)
    _seed_identical_pair(home, workspace_id, root)
    workspace, before, _ = _discovered(home, root)
    assert len(before) == 1
    rows = workspace_mapping_summaries(workspace)
    assert [(row.note, row.problem) for row in rows] == [("valid pair", ""), ("ignored identical duplicate", "duplicate")]

    # What the mapping dialog's remove button on the ignored row does.
    registries = load_product_registries(home)
    remove_workspace_mapping(registries.workspaces, workspace_id, old_path=str(HISTORICAL), new_path=str(root),
                             ignored=True, home=home)
    saved = json.loads(registry_path(home).read_text())["workspaces"][0]["path_mappings"]
    assert [row["note"] for row in saved] == ["valid pair"]
    workspace, after, roots = _discovered(home, root)
    assert after == before and roots == [str(HISTORICAL)]
    assert workspace.ignored_path_mappings == []
    assert apply_path_mappings_to_value(f"{HISTORICAL}/x", workspace.path_mappings) == f"{root}/x"

    # An unrelated save afterwards round-trips the kept binding.
    target = tmp_path / "refs"
    target.mkdir()
    registries = load_product_registries(home)
    add_workspace_mapping(registries.workspaces, workspace_id, old_path="/old/refs", new_path=str(target), home=home)
    workspace, again, _ = _discovered(home, root)
    assert again == before
    assert [(m.old_path, m.note) for m in workspace.path_mappings] == [(str(HISTORICAL), "valid pair"), ("/old/refs", "")]


def test_identical_pair_removal_without_row_flag_still_keeps_the_applied_mapping(tmp_path):
    """The call shape of the review's adjacent_probe.py (old and new only, no row identity)."""
    home, root, workspace_id = _workspace(tmp_path)
    _seed_identical_pair(home, workspace_id, root)
    registries = load_product_registries(home)
    remove_workspace_mapping(registries.workspaces, workspace_id, old_path=str(HISTORICAL), new_path=str(root), home=home)
    workspace, found, _ = _discovered(home, root)
    assert len(found) == 1
    assert [m.note for m in workspace.path_mappings] == ["valid pair"] and workspace.ignored_path_mappings == []


def test_removing_the_applied_row_removes_only_that_row(tmp_path):
    home, root, workspace_id = _workspace(tmp_path)
    _seed_identical_pair(home, workspace_id, root)
    registries = load_product_registries(home)
    remove_workspace_mapping(registries.workspaces, workspace_id, old_path=str(HISTORICAL), new_path=str(root),
                             ignored=False, home=home)
    saved = json.loads(registry_path(home).read_text())["workspaces"][0]["path_mappings"]
    assert [row["note"] for row in saved] == ["ignored identical duplicate"]
    # With its twin gone the remaining copy is no longer a duplicate, so it applies on the next load.
    workspace, found, _ = _discovered(home, root)
    assert len(found) == 1 and len(workspace.path_mappings) == 1
