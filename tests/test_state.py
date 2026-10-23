import time

from pass_rofi.models import ItemRef
from pass_rofi.state import StateStore


def ref():
    return ItemRef("s1", "i1", "GitHub", ("https://github.com",), "Personal", "Login")


def test_stage_defaults_to_list(tmp_path):
    state = StateStore(tmp_path)
    assert state.get_stage() == "list"
    state.set_stage("fields")
    assert StateStore(tmp_path).get_stage() == "fields"


def test_last_copied_roundtrip_and_age(tmp_path):
    state = StateStore(tmp_path)
    assert state.get_last_copied() is None
    assert state.last_copy_age() is None
    state.set_last_copied(ref(), timestamp=time.time() - 5)
    again = StateStore(tmp_path)
    assert again.get_last_copied() == ref()
    assert 4 <= again.last_copy_age() <= 10


def test_clear_last_copied(tmp_path):
    state = StateStore(tmp_path)
    state.set_last_copied(ref())
    state.clear_last_copied()
    assert state.get_last_copied() is None
    assert state.last_copy_age() is None


def test_clip_marker(tmp_path):
    state = StateStore(tmp_path)
    assert state.get_clip_marker() == ""
    state.set_clip_marker("abc")
    assert StateStore(tmp_path).get_clip_marker() == "abc"


def test_state_files_not_world_readable(tmp_path):
    state = StateStore(tmp_path)
    state.set_last_copied(ref())
    assert state.last_item_path.stat().st_mode & 0o077 == 0
    assert state.stamp_path.stat().st_mode & 0o077 == 0
