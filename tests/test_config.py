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


def test_none_auth_does_not_read_secret_environment(tmp_path, monkeypatch):
    import solomon_agent.config as config_module

    class GuardedEnvironment(dict):
        def get(self, key, default=None):
            if key in {"SOLOMON_API_KEY", "PRIVATE_PROXY_KEY"}:
                raise AssertionError("No-auth mode must not inspect key variables")
            return super().get(key, default)

    monkeypatch.setattr(config_module.os, "environ", GuardedEnvironment())
    path = tmp_path / "config.toml"
    path.write_text(
        'auth_mode = "none"\nmodel = "test"\n'
        'base_url = "http://127.0.0.1:3458/v1"\napi_key_env = "PRIVATE_PROXY_KEY"\n'
    )
    config = Config.load(path)
    assert config.auth_mode == "none"
    assert config.api_key == ""
    config.require_model()


def test_unknown_auth_mode_rejected(tmp_path):
    with pytest.raises(ValueError, match="auth_mode"):
        Config(tmp_path, auth_mode="automatic")


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        "",
        "ftp://example.com/v1",
        "/v1",
        "http://",
        "http://localhost:bad/v1",
        "http://user:password@localhost/v1",
        "http://bad host/v1",
    ],
)
def test_none_auth_requires_valid_explicit_endpoint(tmp_path, endpoint):
    with pytest.raises(ValueError, match="base_url"):
        Config(tmp_path, model="test", auth_mode="none", base_url=endpoint).require_model()


def test_none_auth_still_requires_model(tmp_path):
    with pytest.raises(ValueError, match="model"):
        Config(tmp_path, auth_mode="none", base_url="http://localhost/v1").require_model()


def test_doctor_reports_auth_mode_without_credentials(tmp_path, monkeypatch, capsys):
    import json
    import sys

    from solomon_agent.cli import main

    monkeypatch.setattr(
        Config,
        "load",
        lambda *args: Config(
            tmp_path, auth_mode="none", base_url="http://localhost/v1", api_key="ignored-secret"
        ),
    )
    monkeypatch.setattr(sys, "argv", ["solomon-agent", "doctor"])
    main()
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["auth_mode"] == "none"
    assert report["api_key_configured"] is False
    assert "ignored-secret" not in output


def test_none_auth_ignores_both_key_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLOMON_API_KEY", "fake-global-solomon-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-global-openai-key")
    path = tmp_path / "config.toml"
    path.write_text('auth_mode = "none"\nmodel = "test"\nbase_url = "http://localhost/v1"\n')
    config = Config.load(path)
    config.require_model()
    assert config.api_key == ""


@pytest.mark.parametrize("effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
def test_explicit_reasoning_effort_loaded(tmp_path, effort):
    path = tmp_path / "config.toml"
    path.write_text(f'reasoning_effort = "{effort}"\n')
    assert Config.load(path).reasoning_effort == effort


def test_reasoning_effort_default_omitted_and_invalid_rejected(tmp_path):
    assert Config(tmp_path).reasoning_effort is None
    with pytest.raises(ValueError, match="reasoning_effort"):
        Config(tmp_path, reasoning_effort="automatic")
