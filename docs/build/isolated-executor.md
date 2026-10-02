# BE05 isolated Python and staged workspaces

The certified envelope is deliberately narrow: Linux x86-64, local backend,
stateless Python computation, selected standard-library modules, no tool RPC,
network, project interpreter, third-party packages, executable children, or host
input files through the model-facing `execute_code` interface. A configured
identity always selects this path, including direct handler calls. Unsupported
OS, backend, namespace, mount, capability or seccomp facilities fail closed. The
legacy unbound session kernel does not gain isolation certification.

## Enforced boundary

The trusted supervisor launches with the shared environment builder followed by
a two-key non-secret allowlist, close-on-exec descriptors, and Python `-I -S`.
It creates private user, mount, network and PID namespaces through the installed
`unshare`. No host-global configuration is changed. Inside these namespaces a
separate worker enters a private chroot. Only the interpreter's standard-library
files and an explicit immutable input snapshot are mounted read-only; package
startup directories are covered by empty read-only mounts. There is no mounted
`/proc`, host home, repository, device tree or host socket.

The worker drops all effective/permitted/inheritable and current bounding
capabilities, locks no-root/setuid-fixup securebits, sets no-new-privileges, then
loads a native-architecture libseccomp syscall allowlist. Socket creation,
exec/fork/clone, namespace/mount operations, cross-process inspection/signalling,
credential changes, device operations and io_uring are outside that allowlist.
The supervisor receives a readiness byte only after these steps succeed. The
untrusted worker does not inherit the supervisor's result protocol descriptor.
Generated Python can use native code; the boundary is enforced by the OS, not a
Python import blacklist or a prompt.

The accepted threat model is generated code within this worker. The installed
interpreter/stdlib, supervisor and host application are trusted. This is not a
claim against a compromised kernel, malicious same-user host process, hardware
side channels or hostile multi-tenant hosts. Other platforms and subprocess/MCP
servers are not certified by this adapter.

## Finite work and results

Maximum useful runtime is 30 seconds, with a 32-second overall envelope including
bootstrap and teardown. CPU and address-space hard limits are applied; address
space is 256 MiB, one file is at most 2 MiB, and each of `/outputs` and `/tmp` is a
separate 8 MiB tmpfs with 512 inodes. Output export accepts at most 128 examined
entries and 8 MiB of regular one-link file content. Each log stream retains at
most 64 KiB; excess is drained and marked truncated. Forking and threading are
unsupported. Some extension modules with additional shared-library dependencies
are unavailable in this initial runtime view.

Before launch the strict adapter rechecks the live identity/run/owner, consumes a
short-lived capability binding the code hash, workspace manifest, resource root
and finite wall envelope, then dispatches its BE03 executor reservation. The
reservation is acquired at the concrete handler edge so direct registry calls
cannot evade it. The outer budget tool scope validates adapter provenance and
does not acquire a duplicate slot. Remaining root deadline and request timeout
can shorten the execution allowance; insufficient launch/teardown time refuses.
Cancellation kills the process group and namespace. Acknowledged local termination
releases the slot; unacknowledged termination retains it and reports uncertainty.
A stopped local computation is never an external effect reversal.

The supervisor exports output bytes only after worker exit, rejecting symlinks,
hardlinks, devices, oversized files and excessive entry counts. The parent checks
this bounded envelope again, writes private staging files, and returns content
hashes plus the manifest reference. Partial files may be retained with a failed
execution receipt; their presence does not mean the computation succeeded.
Stages are retained for inspection, not automatically promoted or garbage-collected
by this phase. BE06/BE07 own durable effect recovery, retention and artifact UX.

## Workspace review and promotion

`StagedWorkspace` snapshots explicitly selected regular source files; all path
components are opened without following symlinks. Input snapshots are checked
against exact file membership and hashes before use. The manifest binds base
revision, inputs, output content hashes and staged effects.

The initial promotion API handles one explicitly reviewed **new** file. It
requires a target recorded as absent in the original base, the live base revision
and exact approved manifest digest, rechecks
parent input hashes and staged output bytes, then atomically links the new file
into the destination with no replacement. Existing target files and symlinks
always conflict, even when a file appears unchanged. Parent directory symlinks,
changed inputs, stale revisions and changed approval/content hashes refuse.
Existing-file edits and multi-file transactional merges remain staged for later
adapters. This helper is not exposed as a model tool and is never called
implicitly after execution. It does not claim a transactional snapshot against
uncoordinated concurrent external writers; future transactional edits require an
authoritative revision/locking protocol.

## Evidence

Focused tests exercise the existing registry, a real identity-bound SQLite run,
capability consumption, budget reservation and the real namespace worker. Harmless
seeded attacks cover host file/env/descriptor reads, host writes, symlink escape,
IPv4/IPv6/Unix sockets, fork/exec, immutable inputs, unavailable tool RPC, memory,
file/tmpfs/log limits, deadline/cancellation, rejected output types and stale-base
promotion. Mocked tests are limited to unavailable-enforcement and unacknowledged
termination branches; they do not replace the kernel probes.

The development host's `unshare` namespace and tmpfs operations succeeded.
`bwrap` could not create its loopback NETLINK_ROUTE socket; Landlock returned
ENOSYS. Neither was used as an enforcement claim or bypassed. Exact test receipts
and tested kernel/interpreter versions belong in the phase validation manifest.
