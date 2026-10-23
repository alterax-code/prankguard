"""ConfigStore : défauts, bornes, choix, types, persistance, abonnés."""
import json

import pytest

from prankguard.config import _BOUNDS, ConfigStore, Settings


@pytest.fixture
def path(tmp_path):
    return tmp_path / "sub" / "config.json"


def test_defaults_when_no_file(path):
    store = ConfigStore(path)
    assert store.s == Settings()
    assert store.s is store.settings
    assert not path.exists()                    # rien n'est écrit tant qu'on ne change rien


def test_update_applies_and_persists(path):
    store = ConfigStore(path)
    applied = store.update({"stranger_delay": 5.0, "lock_on_absence": True})
    assert applied == {"stranger_delay": 5.0, "lock_on_absence": True}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["stranger_delay"] == 5.0 and data["lock_on_absence"] is True
    assert not path.with_suffix(".tmp").exists()


def test_update_unchanged_value_is_not_reported(path):
    store = ConfigStore(path)
    assert store.update({"stranger_delay": Settings().stranger_delay}) == {}
    assert not path.exists()


def test_unknown_keys_ignored(path):
    store = ConfigStore(path)
    assert store.update({"nope": 1, "__class__": 2}) == {}


@pytest.mark.parametrize("name,value,expected", [
    ("match_threshold", 5.0, 0.70), ("match_threshold", -1.0, 0.25),
    ("stranger_delay", 0.0, 0.3), ("stranger_delay", 1e6, 30.0),
    ("surfer_delay", 0.0, 0.5), ("absence_delay", 0, 3.0), ("absence_delay", 99999, 600.0),
    ("camera_lost_delay", -3, 1.0), ("rearm_delay", -1, 0.0), ("rearm_delay", 1e3, 60.0),
    ("min_face_size", 0.0, 0.04), ("max_yaw", 9, 1.0),
    ("camera_index", 99, 9), ("camera_index", -4, 0),
    ("camera_width", 10, 320), ("camera_width", 99999, 1920),
    ("camera_height", 1, 240), ("alarm_volume", 500, 100), ("alarm_volume", -5, 0),
])
def test_numeric_values_are_clamped(path, name, value, expected):
    store = ConfigStore(path)
    store.update({name: value})
    got = getattr(store.s, name)
    assert got == expected
    assert type(got) is type(getattr(Settings(), name))


def test_every_bound_is_consistent_with_its_default():
    d = Settings()
    for name, (lo, hi) in _BOUNDS.items():
        assert lo <= getattr(d, name) <= hi, name


def test_numeric_strings_are_coerced_and_garbage_rejected(path):
    store = ConfigStore(path)
    assert store.update({"stranger_delay": "2.5"}) == {"stranger_delay": 2.5}
    assert store.update({"stranger_delay": "abc"}) == {}
    assert store.update({"camera_index": None}) == {}
    assert store.s.stranger_delay == 2.5


@pytest.mark.parametrize("name", ["surfer_action", "device_action"])
def test_invalid_choice_rejected(path, name):
    store = ConfigStore(path)
    before = getattr(store.s, name)
    assert store.update({name: "bogus"}) == {}
    assert getattr(store.s, name) == before


def test_valid_choices_accepted(path):
    store = ConfigStore(path)
    for v in ("off", "warn", "lock"):
        store.update({"surfer_action": v})
        assert store.s.surfer_action == v
    for v in ("off", "notify", "ask", "lock"):
        store.update({"device_action": v})
        assert store.s.device_action == v


@pytest.mark.parametrize("bad", ["yes", 1, 0, None, "true", 1.0])
def test_non_bool_rejected_for_bool_fields(path, bad):
    store = ConfigStore(path)
    before = store.s.anti_spoof
    assert store.update({"anti_spoof": bad}) == {}
    assert store.s.anti_spoof is before


def test_bool_fields_accept_bool(path):
    store = ConfigStore(path)
    assert store.update({"anti_spoof": False}) == {"anti_spoof": False}
    assert store.s.anti_spoof is False


def test_trusted_devices_roundtrip(path):
    store = ConfigStore(path)
    applied = store.update({"trusted_devices": ["046D:C52B", "USB\\X", 7]})
    assert applied["trusted_devices"] == ("046D:C52B", "USB\\X", "7")
    assert isinstance(store.s.trusted_devices, tuple)
    assert store.s.to_dict()["trusted_devices"] == ["046D:C52B", "USB\\X", "7"]
    assert json.loads(path.read_text())["trusted_devices"] == ["046D:C52B", "USB\\X", "7"]
    reloaded = ConfigStore(path)
    assert reloaded.s.trusted_devices == ("046D:C52B", "USB\\X", "7")
    store.update({"trusted_devices": []})
    assert store.s.trusted_devices == ()


def test_trusted_devices_rejects_non_list_and_caps_size(path):
    store = ConfigStore(path)
    assert store.update({"trusted_devices": "046D:C52B"}) == {}
    store.update({"trusted_devices": [str(i) for i in range(1000)]})
    assert len(store.s.trusted_devices) == 500


def test_persistence_across_reload(path):
    ConfigStore(path).update({"match_threshold": 0.5, "surfer_action": "lock", "camera_index": 2,
                              "start_with_windows": True})
    s = ConfigStore(path).s
    assert (s.match_threshold, s.surfer_action, s.camera_index, s.start_with_windows) == (0.5, "lock", 2, True)


@pytest.mark.parametrize("content", ["{not json", "", "\x00\x01garbage"])
def test_corrupted_file_gives_defaults(path, content):
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert ConfigStore(path).s == Settings()


def test_invalid_values_in_file_are_ignored_individually(path):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"match_threshold": "abc", "surfer_action": "bogus", "anti_spoof": "yes",
                                "stranger_delay": 99999, "lock_on_absence": True, "unknown_key": 1}),
                    encoding="utf-8")
    s = ConfigStore(path).s
    d = Settings()
    assert s.match_threshold == d.match_threshold
    assert s.surfer_action == d.surfer_action
    assert s.anti_spoof == d.anti_spoof
    assert s.stranger_delay == 30.0                     # borné au chargement aussi
    assert s.lock_on_absence is True


@pytest.mark.parametrize("content", ["[]", "null", "3", '"str"'])
def test_valid_json_but_not_an_object_gives_defaults(path, content):
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert ConfigStore(path).s == Settings()


def test_listener_receives_applied_changes(path):
    store = ConfigStore(path)
    calls = []
    store.subscribe(calls.append)
    store.update({"stranger_delay": 4.0, "surfer_action": "bogus"})
    assert calls == [{"stranger_delay": 4.0}]


def test_failing_listener_does_not_break_update_or_other_listeners(path):
    store = ConfigStore(path)
    seen = []

    def bad(_):
        raise RuntimeError("boom")

    store.subscribe(bad)
    store.subscribe(seen.append)
    assert store.update({"alarm_enabled": True}) == {"alarm_enabled": True}
    assert seen == [{"alarm_enabled": True}]
    assert ConfigStore(path).s.alarm_enabled is True


def test_legacy_lock_if_away_migrates_to_ask(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"device_action": "lock_if_away"}', encoding="utf-8")
    assert ConfigStore(path).s.device_action == "ask"


def test_device_categories_filtered_and_deduplicated(path):
    store = ConfigStore(path)
    store.update({"device_categories": ["storage", "bogus", "storage", "printer"]})
    assert store.s.device_categories == ("storage", "printer")


def test_device_names_must_be_a_mapping(path):
    store = ConfigStore(path)
    store.update({"device_names": ["x"]})
    assert store.s.device_names == {}
    store.update({"device_names": {"1532:00B4": "Razer"}})
    assert store.s.device_names == {"1532:00B4": "Razer"}
