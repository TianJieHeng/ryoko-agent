"""Sanitizer faults must never spawn a privileged driver with ambient secrets."""
import builtins
from unittest.mock import Mock

import pytest

from tools.computer_use import cua_backend, permissions


@pytest.mark.parametrize("surface", ["driver", "permissions"])
@pytest.mark.parametrize("fault", ["import", "sanitize"])
def test_sanitizer_fault_refuses_before_subprocess(monkeypatch, surface, fault):
    from tools.environments import local
    secret = "fixture-must-not-enter-child"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    launch = Mock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr(cua_backend.subprocess, "run", launch)
    if fault == "import":
        original = builtins.__import__
        def refuse(name, *args, **kwargs):
            if name == "tools.environments.local":
                raise ImportError("fixture unavailable sanitizer")
            return original(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", refuse)
    else:
        def refuse(**kwargs):
            raise RuntimeError(secret)
        monkeypatch.setattr(local, "hermes_subprocess_env", refuse)
    with pytest.raises(RuntimeError, match="sanitization unavailable") as caught:
        if surface == "driver":
            cua_backend._run_driver("fixture-driver", "doctor", timeout=1)
        else:
            permissions._run("fixture-driver", "doctor", timeout=1)
    assert secret not in str(caught.value)
    launch.assert_not_called()


def test_driver_does_not_honor_terminal_credential_override(monkeypatch):
    from tools.environments.local import _HERMES_PROVIDER_ENV_FORCE_PREFIX
    override = _HERMES_PROVIDER_ENV_FORCE_PREFIX + "ANTHROPIC_API_KEY"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-provider")
    monkeypatch.setenv(override, "fixture-force-pass")
    env = cua_backend.sanitized_cua_driver_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert override not in env
    assert env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] == "0"
