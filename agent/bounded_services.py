"""Two finite local services over exact project artifacts, never a remote shell.

Preparation discloses the complete route before transferring bytes. Each pure
stage commits its bytes and digest receipt atomically on the existing database
writer. Restart may repeat only an uncommitted pure computation; committed
stages are loaded and verified, and no publication or external effect occurs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import asdict, dataclass

from agent.project_context import project_access
from agent.result_artifacts import artifact_actor, read_project_artifact
from hermes_state_runtime import RuntimeStoreError

MAX_SERVICE_BYTES = 65536
MAX_PIPELINES = 256


def require(condition, code):
    if not condition:
        raise RuntimeStoreError(code, code.replace("_", " "))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ServiceManifest:
    service_id: str
    capability: str
    version: int
    input_mime: str
    output_mime: str
    input_schema: str
    output_schema: str
    max_input_bytes: int = MAX_SERVICE_BYTES
    max_output_bytes: int = MAX_SERVICE_BYTES
    max_seconds: float = 2.0
    processing_location: str = "local_profile_store"
    destination: str = "local_profile_store"
    authentication: str = "live_identity_and_project_grant"
    data_policy: str = "same_profile_no_network_no_publication"


NORMALIZE = ServiceManifest("local.text.normalize", "utf8_normalization", 1,
    "text/markdown", "text/plain", "bounded_utf8_no_nul", "lf_normalized_utf8")
DOCUMENT = ServiceManifest("local.document.structure", "document_structure", 1,
    "text/plain", "application/json", "lf_normalized_utf8", "document_text_and_headings_v1")
SERVICES = (NORMALIZE, DOCUMENT)


def _normalize(data):
    text = data.decode("utf-8")
    require("\0" not in text, "service_invalid_text")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _structure(data):
    text = data.decode("utf-8")
    # This is syntax extraction, not model summarization or instruction execution.
    headings = []
    fence = False
    for index, line in enumerate(text.splitlines()):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if not fence and line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if 1 <= level <= 6 and line[level:level + 1] == " ":
                require(len(headings) < 256, "service_heading_bound")
                headings.append({"line": index + 1, "level": level, "text": line[level + 1:]})
    return canonical({"schema_version": 1, "text": text, "headings": headings}).encode("utf-8")


_ADAPTERS = {NORMALIZE.service_id: _normalize, DOCUMENT.service_id: _structure}


class BoundedServices:
    """A trusted host can take a service offline; RPC callers cannot register code."""

    def __init__(self, context, db, *, unavailable=()):
        self.context, self.db = context, db
        self.actor = artifact_actor(context)
        self.access = project_access(context)
        self.unavailable = frozenset(unavailable)
        require(self.unavailable <= {service.service_id for service in SERVICES}, "service_unknown")

    def _authenticate(self):
        from tools.capability_broker import require_live_policy
        require(require_live_policy(require_run=False) == self.context, "identity_mismatch")

    def capabilities(self):
        self._authenticate()
        return [{**asdict(service), "health": "unavailable" if service.service_id in self.unavailable else "ready"}
                for service in SERVICES]

    def _placement(self):
        from agent.executor_capabilities import authenticate_local_executor, executor_inspection, require_executor
        for row in executor_inspection(self.context):
            if row["available"] and {"document.utf8", "document.extract"} <= set(row["capabilities"]):
                reference = {key: row[key] for key in ("executor_id", "generation", "capability_digest", "location")}
                require_executor(self.context, reference, capability="document.utf8")
                return reference
        return authenticate_local_executor(self.context).reference()

    def _row(self, conn, pipeline_id):
        row = conn.execute("SELECT * FROM bounded_service_pipelines WHERE pipeline_id=?", (pipeline_id,)).fetchone()
        require(row is not None, "service_pipeline_not_found")
        require(json.loads(row["owner_json"]) == self.actor, "identity_mismatch")
        root = self.db._mission_owner_on_conn(conn, self.context.identity.session_id, self.actor)
        require(row["session_id"] == root, "service_session_mismatch")
        require(digest(row["manifest_json"].encode()) == row["manifest_sha256"], "service_digest_mismatch")
        return row

    def prepare(self, session_id, project_id, request_id, artifact_id, version):
        self._authenticate()
        require(isinstance(request_id, str) and 0 < len(request_id) <= 256, "invalid_command")
        with self.access.guard(project_id, self.actor, "read"):
            with self.db._runtime_read() as conn:
                session_id = self.db._mission_owner_on_conn(conn, session_id, self.actor)
            source = self.db.read_artifact_version(artifact_id, version, self.actor, access=self.access)
            descriptor = source["descriptor"]
            require(source["project_id"] == project_id and descriptor["mime"] == NORMALIZE.input_mime,
                    "service_input_format")
            require(descriptor["size"] <= MAX_SERVICE_BYTES, "service_input_bound")
            pipeline_id = "pipeline_" + digest(canonical({"owner": self.actor, "session": session_id,
                "request": request_id}).encode())
            manifest = {"schema_version": 1, "pipeline_id": pipeline_id, "project_id": project_id,
                "executor": self._placement(),
                "source": {key: descriptor[key] for key in ("artifact_id", "version", "sha256", "size", "mime")},
                "stages": [asdict(service) for service in SERVICES],
                "transfer": {"input_bytes": descriptor["size"], "max_intermediate_bytes": MAX_SERVICE_BYTES,
                    "max_output_bytes": MAX_SERVICE_BYTES, "from": "local_project_artifact",
                    "to": "local_profile_store", "remote_bytes": 0},
                "publication": "private_staged_output_requires_separate_artifact_approval"}
            encoded = canonical(manifest)

            def write(conn):
                # Only a currently bound stored session can own a protocol record.
                self.db._mission_owner_on_conn(conn, session_id, self.actor)
                old = conn.execute("SELECT manifest_json FROM bounded_service_pipelines WHERE pipeline_id=?", (pipeline_id,)).fetchone()
                if old:
                    previous = json.loads(old[0])
                    require({key: value for key, value in previous.items() if key != "executor"}
                            == {key: value for key, value in manifest.items() if key != "executor"}, "idempotency_conflict")
                    # Re-preparing explicitly discloses a fresh authenticated local
                    # placement. Executing still needs this new exact preview digest.
                    if previous != manifest:
                        conn.execute("UPDATE bounded_service_pipelines SET manifest_json=?,manifest_sha256=? WHERE pipeline_id=?",
                                     (encoded, digest(encoded.encode()), pipeline_id))
                else:
                    require(conn.execute("SELECT COUNT(*) FROM bounded_service_pipelines").fetchone()[0] < MAX_PIPELINES,
                            "service_capacity")
                    conn.execute("INSERT INTO bounded_service_pipelines VALUES(?,?,?,?,?,?,?,?)",
                        (pipeline_id, canonical(self.actor), session_id, project_id, request_id,
                         encoded, digest(encoded.encode()), time.time()))
                return {"manifest": manifest, "manifest_sha256": digest(encoded.encode())}
            return self.db._execute_write(write)

    def _load(self, pipeline_id):
        self._authenticate()
        with self.db._runtime_read() as conn:
            row = dict(self._row(conn, pipeline_id))
        with self.access.guard(row["project_id"], self.actor, "read"):
            return row, json.loads(row["manifest_json"])

    def _stage(self, pipeline_id, stage):
        with self.db._runtime_read() as conn:
            row = conn.execute("SELECT receipt_json,output_bytes FROM bounded_service_stages WHERE pipeline_id=? AND stage=?",
                               (pipeline_id, stage)).fetchone()
        if row is None:
            return None
        receipt, data = json.loads(row[0]), bytes(row[1])
        service = SERVICES[stage]
        require(receipt["pipeline_id"] == pipeline_id and receipt["stage"] == stage
                and receipt["service_id"] == service.service_id and receipt["service_version"] == service.version
                and receipt["output_mime"] == service.output_mime and receipt["destination"] == service.destination
                and digest(data) == receipt["output_sha256"] and len(data) == receipt["output_size"]
                and receipt["transfer_id"] == digest(canonical({"pipeline_id": pipeline_id, "stage": stage,
                    "input_sha256": receipt["input_sha256"], "output_sha256": receipt["output_sha256"]}).encode()),
                "service_digest_mismatch")
        return receipt, data

    def status(self, pipeline_id):
        row, manifest = self._load(pipeline_id)
        with self.access.guard(row["project_id"], self.actor, "read"):
            receipts = []
            prior_digest, prior_size = manifest["source"]["sha256"], manifest["source"]["size"]
            for index in range(len(SERVICES)):
                record = self._stage(pipeline_id, index)
                if record is not None:
                    receipt = record[0]
                    require(len(receipts) == index and receipt["input_sha256"] == prior_digest
                            and receipt["input_size"] == prior_size, "service_digest_mismatch")
                    receipts.append(receipt)
                    prior_digest, prior_size = receipt["output_sha256"], receipt["output_size"]
        state = "completed" if len(receipts) == len(SERVICES) else "partial" if receipts else "pending"
        return {"pipeline_id": pipeline_id, "state": state, "receipts": receipts,
                "manifest_sha256": row["manifest_sha256"], "next_stage": len(receipts) if state != "completed" else None,
                "publication": manifest["publication"]}

    def execute(self, pipeline_id, manifest_sha256):
        from agent.executor_capabilities import require_executor
        row, manifest = self._load(pipeline_id)
        self.status(pipeline_id)  # Validate any already committed transfer chain before recovery.
        require(manifest_sha256 == row["manifest_sha256"], "service_manifest_mismatch")
        require(manifest["stages"] == [asdict(service) for service in SERVICES], "service_version_unavailable")
        with self.access.guard(row["project_id"], self.actor, "read"):
            data = None
            for index, service in enumerate(SERVICES):
                done = self._stage(pipeline_id, index)
                if done:
                    data = done[1]
                    continue
                if service.service_id in self.unavailable:
                    return {**self.status(pipeline_id), "blocked_reason": "service_unavailable"}
                self._authenticate()
                try:
                    executor = require_executor(self.context, manifest["executor"],
                        capability="document.utf8" if index == 0 else "document.extract")
                except ValueError as error:
                    if getattr(error, "code", "") not in {"executor_disconnected", "executor_capability_unavailable"}:
                        raise
                    return {**self.status(pipeline_id), "blocked_reason": error.code}
                require(executor.location == "local" and executor.max_input_bytes >= service.max_input_bytes
                        and executor.max_output_bytes >= service.max_output_bytes
                        and executor.timeout_seconds >= service.max_seconds, "service_executor_bound")
                if data is None:
                    source = manifest["source"]
                    data = read_project_artifact(self.context, self.db, row["project_id"], source["artifact_id"], source["version"])
                    require(digest(data) == source["sha256"] and len(data) == source["size"], "service_digest_mismatch")
                require(len(data) <= service.max_input_bytes, "service_input_bound")
                started = time.monotonic()
                output = _ADAPTERS[service.service_id](data)
                require(time.monotonic() - started <= service.max_seconds, "service_time_bound")
                require(len(output) <= service.max_output_bytes, "service_output_bound")
                receipt = {"pipeline_id": pipeline_id, "stage": index, "service_id": service.service_id,
                    "service_version": service.version, "input_sha256": digest(data), "input_size": len(data),
                    "output_sha256": digest(output), "output_size": len(output), "output_mime": service.output_mime,
                    "destination": service.destination, "acknowledgment_level": "local_sqlite_commit",
                    "executor": manifest["executor"],
                    "transfer_id": digest(canonical({"pipeline_id": pipeline_id, "stage": index,
                        "input_sha256": digest(data), "output_sha256": digest(output)}).encode())}

                def commit(conn):
                    # Recheck after processing too: a revoked/expired executor
                    # cannot receive a success receipt merely because it began.
                    require_executor(self.context, manifest["executor"],
                        capability="document.utf8" if index == 0 else "document.extract")
                    current = self._row(conn, pipeline_id)
                    require(current["manifest_sha256"] == manifest_sha256, "service_manifest_mismatch")
                    old = conn.execute("SELECT receipt_json FROM bounded_service_stages WHERE pipeline_id=? AND stage=?",
                                       (pipeline_id, index)).fetchone()
                    if old:
                        require(old[0] == canonical(receipt), "service_digest_mismatch")
                    else:
                        conn.execute("INSERT INTO bounded_service_stages VALUES(?,?,?,?,?)",
                                     (pipeline_id, index, canonical(receipt), output, time.time()))
                self.db._execute_write(commit)
                data = output
        return self.status(pipeline_id)

    def output(self, pipeline_id):
        row, _manifest = self._load(pipeline_id)
        self.status(pipeline_id)
        with self.access.guard(row["project_id"], self.actor, "read"):
            stage = self._stage(pipeline_id, len(SERVICES) - 1)
            require(stage is not None, "service_output_pending")
            return {"pipeline_id": pipeline_id, "receipt": stage[0],
                    "content_base64": base64.b64encode(stage[1]).decode("ascii")}
