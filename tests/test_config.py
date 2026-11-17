from pass_tui.config import Config, DEFAULT_BW_SERVER


def test_defaults():
    config = Config.from_env({})
    assert config.bw_bin == "bw"
    assert config.bw_server == "https://bitwarden.eu"
    assert DEFAULT_BW_SERVER == "https://bitwarden.eu"
    assert config.cache_ttl == 600
    assert config.reopen_seconds == 20
    assert config.verbose is False


def test_bw_bin_and_server_overrides():
    config = Config.from_env(
        {"PASS_TUI_BW_BIN": "/opt/bw", "PASS_TUI_BW_SERVER": "https://vault.example.com"}
    )
    assert config.bw_bin == "/opt/bw"
    assert config.bw_server == "https://vault.example.com"


def test_legacy_pass_cli_env_name_still_sets_binary():
    config = Config.from_env({"PASS_TUI_PASS_CLI": "/old/path"})
    assert config.bw_bin == "/old/path"


def test_cache_dir_and_ttl_env():
    config = Config.from_env({"PASS_TUI_CACHE_DIR": "/tmp/x", "PASS_TUI_CACHE_TTL": "60"})
    assert config.cache_dir == "/tmp/x"
    assert config.cache_ttl == 60
