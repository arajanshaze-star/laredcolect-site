import json
import sys
from unittest.mock import Mock

import pytest

import safety
import publish
import produce


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Unexpected network access")
    monkeypatch.setattr("requests.sessions.Session.request", blocked)


@pytest.mark.parametrize("module", [publish, produce])
def test_live_cli_blocks_before_connecting(module, monkeypatch):
    monkeypatch.setenv("LRC_PUBLICATION_ENABLED", "true")
    monkeypatch.setenv("LRC_PRODUCTION_ENABLED", "true")
    service = Mock(side_effect=AssertionError("Must not connect"))
    monkeypatch.setattr(module, "youtube_service", service)
    monkeypatch.setattr(sys, "argv", ["autopilot"])
    with pytest.raises(RuntimeError, match="autorizaciones pendientes"):
        module.main()
    service.assert_not_called()


@pytest.mark.parametrize("module", [publish, produce])
def test_dry_run_is_offline(module, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["autopilot", "--dry-run"])
    module.main()


def test_low_level_side_effects_blocked(tmp_path):
    with pytest.raises(RuntimeError, match="autorizaciones pendientes"):
        publish.upload_file(Mock(), {}, tmp_path / "video.mp4")
    with pytest.raises(RuntimeError, match="autorizaciones pendientes"):
        produce.hf_render("test", 4, 1)
    with pytest.raises(RuntimeError, match="autorizaciones pendientes"):
        produce.synthesize_voice({}, tmp_path / "voice.mp3")


def test_verification_alone_is_not_activation(tmp_path, monkeypatch):
    config = tmp_path / "authorizations.json"
    config.write_text(json.dumps({key: True for key in (
        "youtube_verified", "publication_approved", "provider_apis_verified",
        "billing_limits_verified", "production_approved")}))
    monkeypatch.setattr(safety, "CONFIG", config)
    monkeypatch.delenv("LRC_PUBLICATION_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="activación explícita"):
        safety.require_authorization()
    monkeypatch.setenv("LRC_PUBLICATION_ENABLED", "true")
    safety.require_authorization()
    monkeypatch.delenv("LRC_PRODUCTION_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="activación explícita"):
        safety.require_authorization(production=True)


def test_installation_workflows_only_simulate():
    for kind in ("produce", "publish"):
        workflow = (publish.ROOT / ".github" / "workflows" / f"laredcolect-{kind}.yml").read_text()
        assert "schedule:" not in workflow
        assert "secrets." not in workflow
        assert f"python scripts/{kind}.py --dry-run" in workflow
