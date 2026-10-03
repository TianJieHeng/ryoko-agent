"""Fail-closed corrupt-config guards on gateway, serve, and cron surfaces.

Companion to test_noninteractive_config_guard.py (PR #81988): issue #81952
extended to every non-interactive startup surface.
"""

from __future__ import annotations

import os

import pytest

@pytest.fixture(autouse=True)
def _isolated_config_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_IGNORE_USER_CONFIG", raising=False)
    yield
    os.environ.pop("HERMES_IGNORE_USER_CONFIG", None)

def _write_corrupt_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("model: [unterminated\n", encoding="utf-8")
    return path

class TestGatewayGuard:
    def test_gateway_refuses_corrupt_config(self, tmp_path, capsys):
        from gateway.run import _guard_corrupt_user_config

        _write_corrupt_config(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            _guard_corrupt_user_config()

        assert exc_info.value.code == 2
        assert "Hermes stopped because your settings file" in capsys.readouterr().err

    def test_gateway_allows_valid_config(self, tmp_path):
        from gateway.run import _guard_corrupt_user_config

        (tmp_path / "config.yaml").write_text("model:\n  default: local/test\n")
        _guard_corrupt_user_config()  # must not raise

    def test_gateway_allows_missing_config(self, tmp_path):
        from gateway.run import _guard_corrupt_user_config

        _guard_corrupt_user_config()  # first-run state: must not raise

    def test_gateway_escape_hatch(self, monkeypatch, tmp_path):
        from gateway.run import _guard_corrupt_user_config

        _write_corrupt_config(tmp_path)
        monkeypatch.setenv("HERMES_IGNORE_USER_CONFIG", "1")
        _guard_corrupt_user_config()  # must not raise

class TestCronRunJobGuard:
    def _job(self, **overrides):
        job = {"id": "job-test-1", "name": "guard test", "prompt": "hi"}
        job.update(overrides)
        return job

    def test_run_job_fails_closed_on_corrupt_config(self, tmp_path):
        from cron.scheduler import run_job

        _write_corrupt_config(tmp_path)

        success, output_doc, final_response, error = run_job(self._job())

        assert success is False
        assert error is not None
        assert "Hermes stopped because your settings file" in error
        assert "config.yaml" in error
        assert final_response == ""

    @pytest.mark.parametrize("ignore_config", [False, True])
    @pytest.mark.parametrize("no_agent", [False, True])
    def test_corrupt_identity_policy_never_reaches_job_work(self, monkeypatch, tmp_path, ignore_config, no_agent):
        from cron import scheduler

        _write_corrupt_config(tmp_path)
        if ignore_config:
            monkeypatch.setenv("HERMES_IGNORE_USER_CONFIG", "1")
        calls = []
        monkeypatch.setattr(scheduler, "_run_legacy_job", lambda *args, **kwargs: calls.append("unsafe"))
        success, output_doc, final_response, error = scheduler.run_job(
            self._job(no_agent=no_agent, script="true", deliver="none")
        )
        assert success is False and final_response == ""
        assert error is not None
        expected = "Cron identity policy could not be read" if ignore_config else "Hermes stopped because your settings file"
        assert expected in error
        assert calls == [], "unreadable identity policy cannot authorize even script-only work"

class TestServeGuard:
    def test_serve_headless_refuses_corrupt_config(self, tmp_path, capsys):
        """The `hermes serve` headless path fails closed before startup."""
        from argparse import Namespace

        from hermes_cli import main as main_mod

        _write_corrupt_config(tmp_path)
        args = Namespace(
            headless_backend=True,
            ignore_user_config=False,
            ssh_session_token_file=None,
            ssh_owner_nonce=None,
            status=False,
            stop=False,
        )

        with pytest.raises(SystemExit) as exc_info:
            main_mod.cmd_dashboard(args)

        assert exc_info.value.code == 2
        assert "Hermes stopped because your settings file" in capsys.readouterr().err
