"""Private namespace supervisor for isolated_python (not a public CLI).

This file must be executable with ``python -I -S``: no repository imports, site
startup, inherited descriptors, or credentials. The untrusted worker is a separate
chrooted process. The supervisor alone exports regular output files after it exits.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
import errno
import hashlib  # preload standard extension modules before entering the jail
import json
import math
import os
import pathlib
import resource
import selectors
import signal
import socket
import stat
import sys
import sysconfig
import time
import traceback

MAX_FILES = 128
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 65536

libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                       ctypes.c_ulong, ctypes.c_char_p]
libc.mount.restype = ctypes.c_int


def mount(source, target, kind=None, flags=0, data=None):
    def encode(value):
        return None if value is None else os.fsencode(value)
    if libc.mount(encode(source), encode(target), encode(kind), flags, encode(data)) != 0:
        raise OSError(ctypes.get_errno(), "isolated mount unavailable")


def bind_readonly(source, target):
    mount(source, target, flags=4096)  # MS_BIND
    mount(None, target, flags=4096 | 32 | 1 | 2 | 4)  # bind remount, ro,nosuid,nodev


def prepare_root(root, inputs):
    mount(None, "/", flags=(1 << 18) | 16384)  # private, recursive
    mount("tmpfs", root, "tmpfs", 2 | 4, "size=1m,nr_inodes=1024,mode=755")
    for name in ("inputs", "outputs", "tmp"):
        os.mkdir(os.path.join(root, name), 0o755)
    bind_readonly(inputs, root + "/inputs")
    stdlib = sysconfig.get_path("stdlib")
    os.makedirs(root + stdlib, mode=0o755)
    bind_readonly(stdlib, root + stdlib)
    # Third-party packages are outside the certified interpreter envelope.
    for name in ("site-packages", "dist-packages"):
        target = root + stdlib + "/" + name
        if os.path.isdir(target):
            mount("tmpfs", target, "tmpfs", 1 | 2 | 4, "size=4k,nr_inodes=4,mode=555")
    for name in ("outputs", "tmp"):
        mount("tmpfs", root + "/" + name, "tmpfs", 2 | 4 | 8,
              "size=8m,nr_inodes=512,mode=700")
    mount(None, root, flags=32 | 1 | 2 | 4)  # immutable jail root


def restrict_worker(seccomp):
    # No new privileges, no ambient/bounding capabilities, even for uid 0 in
    # this private user namespace. The filter cannot be removed or loosened.
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "no_new_privs unavailable")
    if libc.prctl(28, 3 | 12, 0, 0, 0) != 0:  # securebits NOROOT and NO_SETUID_FIXUP locked
        raise OSError(ctypes.get_errno(), "securebits unavailable")
    for capability in range(41):
        if libc.prctl(24, capability, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "capability drop unavailable")
    class CapHeader(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
    class CapData(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                    ("inheritable", ctypes.c_uint32)]
    header, data = CapHeader(0x20080522, 0), (CapData * 2)()
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise OSError(ctypes.get_errno(), "capset unavailable")
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x00050000 | errno.EPERM)
    if not context:
        raise RuntimeError("seccomp initialization unavailable")
    # Native-architecture allowlist. libseccomp rejects other ABIs by default.
    # No sockets (including Unix), exec, fork/clone, namespace/mount, process
    # inspection/signalling, io_uring, handles, BPF, devices or credential APIs.
    allowed = (
        "read write readv writev pread64 pwrite64 open openat close lseek "
        "fstat stat lstat newfstatat statx access faccessat faccessat2 "
        "readlink readlinkat getdents getdents64 getcwd chdir fchdir umask "
        "getpid getppid getuid geteuid getgid getegid getgroups gettid uname "
        "brk mmap mprotect munmap mremap madvise futex "
        "rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack "
        "getrandom clock_gettime gettimeofday time nanosleep clock_nanosleep "
        "sched_yield getrusage times ftruncate truncate fsync fdatasync "
        "unlink unlinkat mkdir mkdirat rmdir rename renameat renameat2 "
        "link linkat symlink symlinkat fcntl dup dup2 dup3 exit exit_group"
    ).split()
    try:
        for name in allowed:
            syscall = seccomp.seccomp_syscall_resolve_name(name.encode())
            if syscall >= 0 and seccomp.seccomp_rule_add(context, 0x7FFF0000, syscall, 0) != 0:
                raise RuntimeError("seccomp allowlist unavailable")
        if seccomp.seccomp_load(context) != 0:
            raise OSError(ctypes.get_errno(), "seccomp load unavailable")
    finally:
        seccomp.seccomp_release(context)


def run_worker(request, root, seccomp, stdout_fd, stderr_fd, ready_fd):
    os.dup2(stdout_fd, 1)
    os.dup2(stderr_fd, 2)
    null_fd = os.open("/dev/null", os.O_RDONLY)
    os.dup2(null_fd, 0)
    # Only these four descriptors remain; the protocol pipe is NOT one of them.
    os.dup2(ready_fd, 3)
    os.closerange(4, 65536)
    os.chroot(root)
    os.chdir("/outputs")
    os.environ.clear()
    os.environ.update({"HOME": "/tmp", "TMPDIR": "/tmp", "LANG": "C.UTF-8"})
    sys.path[:] = [sysconfig.get_path("stdlib"), sysconfig.get_path("stdlib") + "/lib-dynload"]
    sys.argv[:] = ["/inputs/code.py"]
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(request["timeout_seconds"]),) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64,) * 2)
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    restrict_worker(seccomp)
    os.write(3, b"1")
    os.close(3)
    try:
        exec(compile(request["code"], "<isolated-code>", "exec"), {"__name__": "__main__"})
    except BaseException:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def collect_outputs(root):
    outputs, total, examined = [], 0, 0
    for current, directories, files in os.walk(root, followlinks=False):
        examined += len(directories) + len(files)
        if examined > MAX_FILES:
            raise ValueError("output entry limit exceeded")
        for directory in directories:
            if os.path.islink(os.path.join(current, directory)):
                raise ValueError("output symlinks are forbidden")
        for filename in sorted(files):
            path = os.path.join(current, filename)
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
                raise ValueError("only bounded regular output files are supported")
            with open(path, "rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("output byte limit exceeded")
            outputs.append({"path": os.path.relpath(path, root),
                            "content_b64": base64.b64encode(data).decode("ascii")})
    return outputs


def main():
    request = json.loads(sys.stdin.buffer.read(1024 * 1024))
    root = request["jail_root"]
    seccomp_path = ctypes.util.find_library("seccomp")
    if not seccomp_path:
        raise RuntimeError("libseccomp is required")
    seccomp = ctypes.CDLL(seccomp_path, use_errno=True)
    prepare_root(root, request["input_root"])
    pipes = [os.pipe() for _ in range(3)]
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        try:
            run_worker(request, root, seccomp, pipes[0][1], pipes[1][1], pipes[2][1])
        except BaseException:
            traceback.print_exc()
            os._exit(125)
    selector = selectors.DefaultSelector()
    chunks = {0: bytearray(), 1: bytearray(), 2: bytearray()}
    truncated = {0: False, 1: False}
    for index, (read_fd, write_fd) in enumerate(pipes):
        os.close(write_fd)
        os.set_blocking(read_fd, False)
        selector.register(read_fd, selectors.EVENT_READ, index)
    deadline = time.monotonic() + request["timeout_seconds"]
    status, timed_out = None, False
    while selector.get_map() or status is None:
        if status is None:
            done, value = os.waitpid(pid, os.WNOHANG)
            if done:
                status = value
            elif time.monotonic() >= deadline:
                os.kill(pid, signal.SIGKILL)
                _, status = os.waitpid(pid, 0)
                timed_out = True
        for key, _ in selector.select(0.02):
            chunk = os.read(key.fd, 65536)
            if not chunk:
                selector.unregister(key.fd)
                os.close(key.fd)
                continue
            index = key.data
            remaining = (MAX_LOG_BYTES if index < 2 else 1) - len(chunks[index])
            if len(chunk) > remaining and index < 2:
                truncated[index] = True
            chunks[index].extend(chunk[:remaining])
    selector.close()
    isolated = chunks[2] == b"1"
    result = {"isolated": isolated, "exit_code": os.waitstatus_to_exitcode(status),
              "timed_out": timed_out, "stdout": chunks[0].decode("utf-8", "replace"),
              "stderr": chunks[1].decode("utf-8", "replace"),
              "stdout_truncated": truncated[0], "stderr_truncated": truncated[1], "outputs": []}
    if isolated:
        try:
            result["outputs"] = collect_outputs(root + "/outputs")
        except (OSError, ValueError) as exc:
            result["output_error"] = str(exc)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        # Startup details go to the trusted parent, never an unbounded traceback.
        print(json.dumps({"isolated": False, "error": "executor enforcement unavailable"}))
        sys.exit(125)
