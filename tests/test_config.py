from pass_tui.config import Config


def test_defaults_enable_dbus_keyring_and_600s_lock():
    config = Config.from_env({})
    assert config.keyring_backend == "dbus"
    assert config.lock_idle_timeout == 600


def test_keyring_backend_override():
    config = Config.from_env({"PASS_TUI_KEYRING_BACKEND": "kernel"})
    assert config.keyring_backend == "kernel"
    config = Config.from_env({"PASS_TUI_KEYRING_BACKEND": "off"})
    assert config.keyring_backend == "off"


def test_lock_idle_timeout_override():
    config = Config.from_env({"PASS_TUI_LOCK_IDLE_TIMEOUT": "900"})
    assert config.lock_idle_timeout == 900
    config = Config.from_env({"PASS_TUI_LOCK_IDLE_TIMEOUT": "0"})
    assert config.lock_idle_timeout == 0
