"""Actual kernel-boundary probes use synthetic, disposable data only."""
import json
import threading
import time

import pytest

from tools.environments.isolated_python import IsolationUnavailable, execute_isolated_python
from tools.workspace_manifest import StagedWorkspace


pytestmark = pytest.mark.platforms("linux")


def workspace(tmp_path):
    return StagedWorkspace.create(tmp_path / "stage")


def test_real_kernel_boundary_and_staged_output(tmp_path, monkeypatch):
    secret = tmp_path / "synthetic-secret"
    secret.write_text("fixture-not-a-real-credential")
    monkeypatch.setenv("RYOKO_SYNTHETIC_SECRET", "ambient-fixture")
    stage = workspace(tmp_path)
    code = f'''
import os, json, socket, ctypes
checks = {{}}
for name, action in [
    ("host_read", lambda: open({str(secret)!r}).read()),
    ("host_write", lambda: open({str(secret)!r}, "w").write("bad")),
    ("parent_proc", lambda: open("/proc/1/environ").read()),
    ("socket_inet", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
    ("socket_unix", lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)),
    ("socket_ipv6", lambda: socket.socket(socket.AF_INET6, socket.SOCK_STREAM)),
    ("fork", os.fork),
    ("exec", lambda: os.execve("/bin/true", ["true"], {{}})),
]:
    try:
        action()
        checks[name] = "ESCAPED"
    except (OSError, PermissionError):
        checks[name] = "denied"
checks["environment"] = "denied" if "RYOKO_SYNTHETIC_SECRET" not in os.environ else "ESCAPED"
# Absolute and symlink paths are both resolved inside the private root.
os.symlink({str(secret)!r}, "/tmp/escape")
try:
    open("/tmp/escape").read()
    checks["symlink"] = "ESCAPED"
except OSError:
    checks["symlink"] = "denied"
open("result.txt", "w").write("useful staged output")
print(json.dumps(checks))
'''
    result = execute_isolated_python(code, workspace=stage, timeout_seconds=5)
    assert result["isolated"] is True
    assert result["status"] == "completed", result
    assert set(json.loads(result["stdout"]).values()) == {"denied"}
    assert secret.read_text() == "fixture-not-a-real-credential"
    assert (stage.outputs / "result.txt").read_text() == "useful staged output"
    assert result["outputs"][0]["sha256"]
    assert not (tmp_path / "result.txt").exists()


def test_immutable_inputs_and_unavailable_tool_rpc(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "input.txt").write_text("accepted input")
    stage = StagedWorkspace.create(tmp_path / "stage", parent_root=parent,
                                   input_paths=("input.txt",), base_revision="revision-A")
    result = execute_isolated_python('''
import json
checks = {}
checks['read'] = open('/inputs/input.txt').read()
try:
    open('/inputs/input.txt', 'w').write('changed')
    checks['write'] = 'ESCAPED'
except OSError:
    checks['write'] = 'denied'
try:
    import hermes_tools
    checks['rpc'] = 'ESCAPED'
except ImportError:
    checks['rpc'] = 'denied'
print(json.dumps(checks))
''', workspace=stage, timeout_seconds=5)
    assert result["status"] == "completed", result
    assert json.loads(result["stdout"]) == {"read": "accepted input", "write": "denied", "rpc": "denied"}
    assert (parent / "input.txt").read_text() == "accepted input"


def test_symlink_outputs_are_rejected(tmp_path):
    result = execute_isolated_python("import os; os.symlink('/etc/passwd', 'bad')",
                                     workspace=workspace(tmp_path), timeout_seconds=5)
    assert result["outputs"] == []
    assert "symlink" in result["output_error"] or "regular" in result["output_error"]


def test_finite_output_and_file_size(tmp_path):
    result = execute_isolated_python("print('x' * 200000); open('large', 'wb').write(b'x' * 3000000)",
                                     workspace=workspace(tmp_path), timeout_seconds=5)
    assert len(result["stdout"].encode()) <= 65536
    assert result["stdout_truncated"] is True
    assert result["exit_code"] != 0
    assert all(item["size"] <= 2 * 1024 * 1024 for item in result["outputs"])


def test_wall_timeout_and_cancellation_terminate_process(tmp_path):
    started = time.monotonic()
    result = execute_isolated_python("while True: pass", workspace=workspace(tmp_path), timeout_seconds=1)
    assert result["status"] in {"failed", "timed_out"}
    assert time.monotonic() - started < 5
    stage = StagedWorkspace.create(tmp_path / "second-stage")
    cancelled = threading.Event()
    timer = threading.Timer(0.5, cancelled.set)
    timer.start()
    try:
        result = execute_isolated_python("while True: pass", workspace=stage, timeout_seconds=5,
                                         is_cancelled=cancelled.is_set)
    finally:
        timer.cancel()
    assert result["status"] == "cancelled"


def test_unavailable_enforcement_never_uses_legacy_kernel(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.environments.isolated_python.shutil.which", lambda *a, **k: None)
    with pytest.raises(IsolationUnavailable):
        execute_isolated_python("open('/tmp/should-not-run','w')", workspace=workspace(tmp_path))


def test_memory_and_aggregate_tmpfs_bound(tmp_path):
    result = execute_isolated_python("x = bytearray(400 * 1024 * 1024)",
                                     workspace=workspace(tmp_path), timeout_seconds=5)
    assert result["status"] == "failed"
    assert "MemoryError" in result["stderr"]
    stage = StagedWorkspace.create(tmp_path / "second")
    result = execute_isolated_python("""
import os
for i in range(12):
    with open('/tmp/chunk-' + str(i), 'wb') as stream:
        stream.write(b'x' * (1024 * 1024))
""", workspace=stage, timeout_seconds=5)
    assert result["status"] == "failed"
    assert "No space left" in result["stderr"]


def test_ambient_python_startup_and_inherited_descriptor_are_absent(tmp_path, monkeypatch):
    import os
    import fcntl
    hook = tmp_path / "hook.py"
    hook.write_text("raise RuntimeError('startup hook ran')")
    monkeypatch.setenv("PYTHONSTARTUP", str(hook))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    secret = tmp_path / "seed"
    secret.write_text("synthetic")
    with secret.open() as stream:
        inherited = fcntl.fcntl(stream.fileno(), fcntl.F_DUPFD, 100)
        os.set_inheritable(inherited, True)
        try:
            result = execute_isolated_python(f"""
import os
assert 'PYTHONPATH' not in os.environ
assert 'PYTHONSTARTUP' not in os.environ
try:
    os.read({inherited}, 100)
    print('ESCAPED')
except OSError:
    print('descriptor denied')
""", workspace=workspace(tmp_path), timeout_seconds=5)
        finally:
            os.close(inherited)
    assert result['status'] == 'completed', result
    assert result['stdout'] == 'descriptor denied\n'


def test_native_syscalls_cannot_bypass_the_python_api(tmp_path):
    result = execute_isolated_python("""
import ctypes, errno, json
libc = ctypes.CDLL(None, use_errno=True)
checks = {}
# Linux x86-64 socket, mount, unshare, and ptrace syscall numbers.
for name, number, args in [
    ('socket', 41, (2, 1, 0)),
    ('mount', 165, (0, 0, 0, 0, 0)),
    ('unshare', 272, (0x10000000,)),
    ('ptrace', 101, (0, 0, 0, 0)),
]:
    result = libc.syscall(number, *args)
    checks[name] = result == -1 and ctypes.get_errno() == errno.EPERM
print(json.dumps(checks))
""", workspace=workspace(tmp_path), timeout_seconds=5)
    assert result['status'] == 'completed', result
    assert set(json.loads(result['stdout']).values()) == {True}
