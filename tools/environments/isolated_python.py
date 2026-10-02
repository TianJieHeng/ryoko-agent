"""Linux computation-only executor with an actual fail-closed OS boundary.

Certified only when unprivileged user/mount/network/PID namespaces, private tmpfs,
chroot, dropped capabilities and libseccomp all succeed in the launched child.
No inherited credentials, host files, executable children, network or tool RPC.
The legacy session kernel is never a fallback for this path.
"""
from __future__ import annotations

import json
import math
import os
import platform
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from tools.workspace_manifest import MAX_TOTAL_BYTES, StagedWorkspace

MAX_TIMEOUT_SECONDS = 30.0
MAX_CODE_BYTES = 512 * 1024
MAX_RECEIPT_BYTES = MAX_TOTAL_BYTES * 2


class IsolationTerminationUncertain(RuntimeError):
    """A started local namespace did not acknowledge termination in time."""


class IsolationUnavailable(RuntimeError):
    """No untrusted instruction was admitted without the required boundary."""


def _stop(proc: subprocess.Popen, deadline: float) -> None:
    # Namespace PID 1 dying also kills every namespace member. killpg covers
    # unshare itself; no generated child may fork or leave this group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=max(0.01, min(1.0, deadline - time.monotonic())))
    except subprocess.TimeoutExpired as exc:
        raise IsolationTerminationUncertain("local executor termination is unacknowledged") from exc


def execute_isolated_python(code: str, *, workspace: StagedWorkspace,
                            timeout_seconds: float = MAX_TIMEOUT_SECONDS,
                            wall_seconds: float | None = None,
                            is_cancelled: Callable[[], bool] = lambda: False) -> dict:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise IsolationUnavailable("isolated Python requires a supported Linux executor")
    unshare = shutil.which("unshare", path="/usr/bin:/bin")
    if unshare is None:
        raise IsolationUnavailable("unshare is required; unsafe fallback is disabled")
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > MAX_CODE_BYTES:
        raise ValueError("code must be nonempty and within the finite input limit")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (float, int)) or not (
            math.isfinite(timeout_seconds) and 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS):
        raise ValueError("isolated timeout must be positive and at most 30 seconds")
    if wall_seconds is None:
        wall_seconds = timeout_seconds + 2.0
    if not math.isfinite(wall_seconds) or not timeout_seconds < wall_seconds <= 32.0:
        raise ValueError("isolated wall envelope must exceed execution time and be at most 32 seconds")
    workspace.verify_inputs()
    if is_cancelled():
        return {"status": "cancelled", "isolated": False, "executed": False, "outputs": []}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ryoko-jail-") as jail:
        request = json.dumps({"code": code, "input_root": str(workspace.inputs), "jail_root": jail,
                              "timeout_seconds": timeout_seconds}).encode()
        helper = Path(__file__).with_name("isolated_python_bootstrap.py")
        # Use the trusted running base interpreter, never a project venv Python
        # selected by generated code. -I -S forbids env/site startup injection.
        command = [unshare, "--user", "--map-root-user", "--mount", "--net", "--pid", "--fork",
                   "--kill-child=SIGKILL", os.path.realpath(sys.executable), "-I", "-S", str(helper)]
        from tools.environments.local import build_subprocess_env
        clean = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        built = build_subprocess_env(clean, inherit_profile_home=False, scrub_secrets=False)
        child_env = {key: value for key, value in built.items() if key in clean and value == clean[key]}
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
                                env=child_env, cwd="/")
        output = bytearray()
        errors = bytearray()
        selector = selectors.DefaultSelector()
        deadline = started + wall_seconds
        stop_reason = None
        try:
            # The request is bounded; supervise the write as well as reads so an
            # unavailable/stalled bootstrap cannot wedge the executor thread.
            os.set_blocking(proc.stdin.fileno(), False)
            os.set_blocking(proc.stdout.fileno(), False)
            os.set_blocking(proc.stderr.fileno(), False)
            selector.register(proc.stdin, selectors.EVENT_WRITE, "in")
            selector.register(proc.stdout, selectors.EVENT_READ, "out")
            selector.register(proc.stderr, selectors.EVENT_READ, "err")
            sent = 0
            while selector.get_map():
                if is_cancelled() or time.monotonic() >= deadline - 0.5:
                    stop_reason = "cancelled" if is_cancelled() else "timed_out"
                    _stop(proc, deadline)
                    break
                for key, _ in selector.select(0.02):
                    if key.data == "in":
                        try:
                            sent += os.write(key.fd, request[sent:sent + 65536])
                        except BrokenPipeError:
                            sent = len(request)
                        if sent == len(request):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    target = output if key.data == "out" else errors
                    target.extend(chunk)
                    if len(output) > MAX_RECEIPT_BYTES or len(errors) > 65536:
                        raise IsolationUnavailable("executor receipt limit exceeded")
            if stop_reason is not None:
                return {"status": stop_reason, "isolated": False, "executed": None,
                        "outputs": [], "duration_seconds": time.monotonic() - started}
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        finally:
            selector.close()
            if proc.poll() is None:
                _stop(proc, deadline)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if not stream.closed:
                    stream.close()
        try:
            receipt = json.loads(output)
        except (ValueError, UnicodeDecodeError) as exc:
            raise IsolationUnavailable("executor failed before a verified confinement receipt") from exc
        if not isinstance(receipt, dict) or receipt.get("isolated") is not True:
            raise IsolationUnavailable("required namespace/chroot/seccomp enforcement unavailable: "
                                       + str(receipt.get("stderr") or errors.decode("utf-8", "replace"))[:1500])
        staged = workspace.accept_outputs(receipt.pop("outputs", []))
        receipt.update(status="timed_out" if receipt.get("timed_out") else (
            "completed" if receipt.get("exit_code") == 0 else "failed"), executed=True,
            duration_seconds=time.monotonic() - started, workspace_root=str(staged.root),
            workspace_manifest_digest=staged.manifest.digest,
            outputs=[{"path": f.path, "sha256": f.sha256, "size": f.size} for f in staged.manifest.outputs],
            isolation_profile="linux-namespace-python-v1", tool_calls_made=0)
        return receipt
