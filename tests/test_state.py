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


def test_last_copied_roundtrip_returns_ref_and_timestamp(tmp_path):
    state = StateStore(tmp_path)
    assert state.get_last_copied() is None
    state.set_last_copied(ref(), timestamp=time.time() - 5)
    again = StateStore(tmp_path)
    got = again.get_last_copied()
    assert got is not None
    last_ref, copied_at = got
    assert last_ref == ref()
    assert 4 <= time.time() - copied_at <= 10


def test_last_copied_missing_stamp_yields_none(tmp_path):
    state = StateStore(tmp_path)
    state.set_last_copied(ref())
    state.stamp_path.unlink()
    assert StateStore(tmp_path).get_last_copied() is None


def test_clear_last_copied(tmp_path):
    state = StateStore(tmp_path)
    state.set_last_copied(ref())
    state.clear_last_copied()
    assert state.get_last_copied() is None


def test_open_item_roundtrip(tmp_path):
    state = StateStore(tmp_path)
    assert state.get_open_item() is None
    state.set_open_item(ref())
    assert StateStore(tmp_path).get_open_item() == ref()
    state.set_open_item(None)
    assert state.get_open_item() is None


def test_open_item_not_set_by_last_copied(tmp_path):
    state = StateStore(tmp_path)
    state.set_last_copied(ref())
    assert state.get_open_item() is None


def test_state_files_not_world_readable(tmp_path):
    state = StateStore(tmp_path)
    state.set_last_copied(ref())
    assert state.last_item_path.stat().st_mode & 0o077 == 0
    assert state.stamp_path.stat().st_mode & 0o077 == 0
