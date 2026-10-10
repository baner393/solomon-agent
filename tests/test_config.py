import pytest

from solomon_agent.config import Config


def test_paths_follow_configuration_and_secret_is_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLOMON_API_KEY", "not-a-real-secret")
    path = tmp_path / "config.toml"
    path.write_text('data_dir = "state"\nmodel = "test"\n[sources]\nsample = "vault"\n')
    config = Config.load(path)
    assert config.data_dir == tmp_path / "state"
    assert config.sources == {"sample": tmp_path / "vault"}
    assert "not-a-real-secret" not in repr(config)


def test_data_directory_cannot_write_inside_source(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('data_dir = "vault/cache"\n[sources]\nsample = "vault"\n')
    with pytest.raises(ValueError):
        Config.load(path)


def test_real_mode_requires_explicit_key(tmp_path):
    with pytest.raises(ValueError):
        Config(tmp_path, model="test").require_model()
