from __future__ import annotations

import pytest

from scpolya_ui.services.preferences_service import Preferences, load_preferences, preferences_path, update_preferences


def test_defaults_when_missing_or_corrupt(tmp_path):
    assert load_preferences(tmp_path) == Preferences(onboarding_done=False, agent_mode=False)
    preferences_path(tmp_path).write_text("not json", encoding="utf-8")
    assert load_preferences(tmp_path) == Preferences()


def test_update_round_trip_keeps_other_fields(tmp_path):
    update_preferences(tmp_path, agent_mode=True)
    assert update_preferences(tmp_path, onboarding_done=True) == Preferences(onboarding_done=True, agent_mode=True)
    assert load_preferences(tmp_path) == Preferences(onboarding_done=True, agent_mode=True)


def test_unknown_keys_are_ignored_and_rejected_on_update(tmp_path):
    preferences_path(tmp_path).write_text('{"agent_mode": true, "future": 1}', encoding="utf-8")
    assert load_preferences(tmp_path) == Preferences(agent_mode=True)
    with pytest.raises(TypeError):
        update_preferences(tmp_path, unknown=True)
