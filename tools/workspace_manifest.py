"""Immutable executor inputs and reviewed, additive output promotion.

A workspace is a staging mechanism, never an execution security boundary. Only
regular, bounded, explicitly named files cross it. Existing parent files are not
replaced: a reviewed edit stays staged until a future transactional merge adapter.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath

MAX_FILES = 128
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


class WorkspaceConflict(ValueError):
    """The accepted input/output snapshot no longer matches the files."""


def checked_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise WorkspaceConflict("invalid workspace path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise WorkspaceConflict("workspace path must be canonical and relative")
    if len(value.encode()) > 1024:
        raise WorkspaceConflict("workspace path too long")
    return value


def _open_root(root: Path) -> int:
    # Resolve every component by descriptor: O_NOFOLLOW on just the leaf would
    # still admit a swapped or symlinked ancestor.
    root = Path(os.path.abspath(root))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _parent_fd(root: Path, relative: str) -> tuple[int, str]:
    parts = checked_path(relative).split("/")
    fd = _open_root(root)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def read_regular(root: Path, relative: str) -> bytes:
    parent, leaf = _parent_fd(root, relative)
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
                raise WorkspaceConflict("only bounded regular files with one link are supported")
            data = bytearray()
            while len(data) <= MAX_FILE_BYTES:
                chunk = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            if len(data) > MAX_FILE_BYTES or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise WorkspaceConflict("file changed during snapshot")
            return bytes(data)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class WorkspaceFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class WorkspaceManifest:
    base_revision: str
    inputs: tuple[WorkspaceFile, ...]
    outputs: tuple[WorkspaceFile, ...] = ()
    staged_effects: tuple[str, ...] = ()
    new_file_targets: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return _digest(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode())


@dataclass(frozen=True)
class StagedWorkspace:
    root: Path
    parent_root: Path | None
    manifest: WorkspaceManifest

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @classmethod
    def create(cls, root: Path, *, parent_root: Path | None = None,
               input_paths: tuple[str, ...] = (), base_revision: str = "empty",
               new_file_targets: tuple[str, ...] = ()) -> StagedWorkspace:
        if not isinstance(base_revision, str) or not base_revision or len(base_revision) > 256:
            raise WorkspaceConflict("a bounded base revision is required")
        if len(input_paths) > MAX_FILES or len(set(input_paths)) != len(input_paths):
            raise WorkspaceConflict("invalid input file count")
        if len(new_file_targets) > MAX_FILES or len(set(new_file_targets)) != len(new_file_targets):
            raise WorkspaceConflict("invalid promotion target count")
        if (input_paths or new_file_targets) and parent_root is None:
            raise WorkspaceConflict("input files require a parent root")
        for name in new_file_targets:
            parent_fd, leaf = _parent_fd(parent_root, name)
            try:
                try:
                    os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise WorkspaceConflict("new-file target already exists at base revision")
            finally:
                os.close(parent_fd)
        root = Path(os.path.abspath(root))
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        (root / "inputs").mkdir(mode=0o700)
        (root / "outputs").mkdir(mode=0o700)
        records, total = [], 0
        for name in sorted(input_paths):
            checked_path(name)
            data = read_regular(parent_root, name)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise WorkspaceConflict("input byte limit exceeded")
            target = root / "inputs" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            target.chmod(0o400)
            records.append(WorkspaceFile(name, _digest(data), len(data)))
        result = cls(root, Path(os.path.abspath(parent_root)) if parent_root is not None else None,
                     WorkspaceManifest(base_revision, tuple(records), new_file_targets=tuple(sorted(new_file_targets))))
        result._save_manifest()
        return result

    def _save_manifest(self) -> None:
        data = asdict(self.manifest)
        data["manifest_digest"] = self.manifest.digest
        temp = self.root / f".manifest-{secrets.token_hex(8)}"
        with temp.open("x") as stream:
            json.dump(data, stream, sort_keys=True)
        os.replace(temp, self.root / "manifest.json")

    def verify_inputs(self) -> None:
        root_fd = _open_root(self.inputs)
        os.close(root_fd)
        observed = set()
        for current, directories, files in os.walk(self.inputs, followlinks=False):
            if any((Path(current) / name).is_symlink() for name in directories):
                raise WorkspaceConflict("input directory symlink is forbidden")
            for name in files:
                observed.add((Path(current) / name).relative_to(self.inputs).as_posix())
            if len(observed) > MAX_FILES:
                raise WorkspaceConflict("input file limit exceeded")
        if observed != {entry.path for entry in self.manifest.inputs}:
            raise WorkspaceConflict("input snapshot contains undeclared files")
        for entry in self.manifest.inputs:
            data = read_regular(self.inputs, entry.path)
            if len(data) != entry.size or _digest(data) != entry.sha256:
                raise WorkspaceConflict("immutable input snapshot changed")

    def accept_outputs(self, entries: list[dict]) -> StagedWorkspace:
        self.verify_inputs()
        if not isinstance(entries, list) or len(entries) > MAX_FILES or self.manifest.outputs:
            raise WorkspaceConflict("invalid output envelope")
        decoded, seen, total = [], set(), 0
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "content_b64"}:
                raise WorkspaceConflict("invalid output record")
            name = checked_path(entry["path"])
            if name in seen or not isinstance(entry["content_b64"], str):
                raise WorkspaceConflict("duplicate or invalid output")
            seen.add(name)
            if len(entry["content_b64"]) > (MAX_FILE_BYTES + 2) // 3 * 4:
                raise WorkspaceConflict("output file too large")
            data = base64.b64decode(entry["content_b64"], validate=True)
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise WorkspaceConflict("output byte limit exceeded")
            decoded.append((name, data))
        records = []
        for name, data in decoded:
            target = self.outputs / name
            target.parent.mkdir(parents=True, exist_ok=True)
            # outputs are broker-created, outside the child's writable filesystem.
            with target.open("xb") as stream:
                stream.write(data)
            target.chmod(0o400)
            records.append(WorkspaceFile(name, _digest(data), len(data)))
        result = replace(self, manifest=replace(self.manifest, outputs=tuple(records),
                                               staged_effects=tuple(x.path for x in records)))
        result._save_manifest()
        return result

    def promote_output(self, relative: str, *, current_base_revision: str,
                       approved_manifest_digest: str) -> Path:
        """Promote one approved new file, atomically refusing every overwrite.

        The caller supplies the authoritative live revision and exact approved
        manifest digest. Existing-file edits deliberately remain staged. Checks
        describe the observed base; unrelated external writers need a higher-level
        transactional revision authority before multi-file edits can be supported.
        """
        if self.parent_root is None or current_base_revision != self.manifest.base_revision:
            raise WorkspaceConflict("base revision conflict")
        if relative not in self.manifest.new_file_targets:
            raise WorkspaceConflict("output target was not accepted as new at the base revision")
        if approved_manifest_digest != self.manifest.digest:
            raise WorkspaceConflict("output approval digest conflict")
        self.verify_inputs()
        for entry in self.manifest.inputs:
            if _digest(read_regular(self.parent_root, entry.path)) != entry.sha256:
                raise WorkspaceConflict("parent input changed")
        record = next((x for x in self.manifest.outputs if x.path == relative), None)
        if record is None:
            raise WorkspaceConflict("output was not in the reviewed manifest")
        data = read_regular(self.outputs, relative)
        if len(data) != record.size or _digest(data) != record.sha256:
            raise WorkspaceConflict("staged output changed")
        fd, leaf = _parent_fd(self.parent_root, relative)
        temp = f".ryoko-promote-{secrets.token_hex(16)}"
        try:
            target = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(target, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temp, leaf, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                except FileExistsError as exc:
                    raise WorkspaceConflict("parent target exists; edit retained for review") from exc
                os.fsync(fd)
            finally:
                os.unlink(temp, dir_fd=fd)
        finally:
            os.close(fd)
        return self.parent_root / relative
