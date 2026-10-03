"""Small owned production service over existing artifact approvals and effects.

The immutable manifest is the job record. There is no second scheduler, mutable
artifact catalog or claim that separately published bundle members are atomic.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from agent.artifact_commands import assert_artifact_dispatch
from agent.project_context import authorize_project
from hermes_cli.domain_media import _read

MAX_JOB_BYTES = 4 * 1024 * 1024
MAX_OUTPUTS = 16


def _json(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode()) > MAX_JOB_BYTES:
        raise ValueError("Domain job metadata exceeds bound")
    return encoded


def _request_json(value):
    count = 0

    def check(item, depth=0):
        nonlocal count
        count += 1
        if depth > 16 or count > 10000:
            raise ValueError("Domain request structure exceeds bound")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("Domain keys must be strings")
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise ValueError("Domain inputs must be finite JSON")
    check(value)
    result = _json(value)
    if len(result.encode()) > 65536:
        raise ValueError("Domain request exceeds 64 KiB")
    return result


@dataclass(frozen=True)
class DomainJob:
    job_id: str
    project_id: str
    adapter: str
    arguments_json: str

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict) or set(record) != {"job_id", "project_id", "adapter", "arguments"}:
            raise ValueError("Exact domain job fields are required")
        for field in ("job_id", "project_id", "adapter"):
            if not isinstance(record[field], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", record[field]):
                raise ValueError("Invalid domain identifier")
        if not isinstance(record["arguments"], dict) or len(record["arguments"]) > 32:
            raise ValueError("Bounded domain arguments required")
        return cls(record["job_id"], record["project_id"], record["adapter"], _request_json(record["arguments"]))

    def to_record(self):
        return {"job_id": self.job_id, "project_id": self.project_id, "adapter": self.adapter,
                "arguments": json.loads(self.arguments_json)}


def build_domain_package(context, db, job, *, admitted_at):
    """Only finite installed adapters; data cannot select imports or execute code."""
    from hermes_cli.domain_media import build_meeting_package, build_creative_package
    from hermes_cli.domain_education import build_tutor_package, build_educator_package
    from hermes_cli.domain_execution import build_coding_package, build_browser_package
    adapters = {"meeting": build_meeting_package, "creative": build_creative_package,
                "tutor": build_tutor_package, "educator": build_educator_package,
                "coding": build_coding_package, "browser_evidence": build_browser_package}
    if job.adapter not in {*adapters, "data", "decision"}:
        raise ValueError("Unsupported domain adapter; no implicit remote fallback")
    authorize_project(context, job.project_id, "read")
    arguments = json.loads(job.arguments_json)
    if any(key in arguments for key in ("project_id", "context", "db", "now")):
        raise ValueError("Domain arguments cannot replace bound authority or clock")
    if job.adapter == "data":
        from hermes_cli.domain_data import build_data_package
        if set(arguments) != {"inputs", "recipe"} or not isinstance(arguments["inputs"], list) or not 1 <= len(arguments["inputs"]) <= 8:
            raise ValueError("Data requires bounded immutable inputs and recipe")
        inputs, refs = [], []
        for source in arguments["inputs"]:
            if not isinstance(source, dict) or set(source) != {"source_id", "ref", "options"}:
                raise ValueError("Data input requires source_id/ref/options")
            raw, mime = _read(context, db, job.project_id, source["ref"])
            inputs.append({"source_id": source["source_id"], "content_bytes": raw, "mime": mime,
                           "expected_sha256": source["ref"]["sha256"], "options": source["options"]})
            refs.append(source["ref"])
        package = build_data_package(inputs, arguments["recipe"])
        package["metadata"]["source_inputs"] = package["metadata"].get("inputs", [])
        package["metadata"]["inputs"] = refs
        return package
    if job.adapter == "decision":
        from hermes_cli.domain_decisions import build_decision_package
        refs = arguments.pop("source_refs", [])
        if not isinstance(refs, list) or len(refs) > 64:
            raise ValueError("Decision source references exceed bound")
        for ref in refs:
            _read(context, db, job.project_id, ref)
        package = build_decision_package(arguments)
        package["metadata"]["inputs"] = refs
        package["metadata"]["validator_manifest"] = {**package["metadata"].get("validator_manifest", {}),
            "source_bytes": "checked" if refs else "not_supplied", "fact_semantics": "caller_declared_unverified"}
        return package
    if job.adapter in {"meeting", "tutor", "browser_evidence"}:
        arguments["now"] = admitted_at
    import inspect
    try:
        inspect.signature(adapters[job.adapter]).bind(context, db, project_id=job.project_id, **arguments)
    except TypeError as exc:
        raise ValueError("Domain argument shape does not match its finite adapter") from exc
    return adapters[job.adapter](context, db, project_id=job.project_id, **arguments)


def prepare_domain_job(run, job):
    from hermes_cli.artifact_store import prepare_artifact
    from hermes_cli.artifact_formats import validate_artifact
    assert_artifact_dispatch(run)
    if not isinstance(job, DomainJob):
        raise ValueError("Typed domain job required")
    authorize_project(run.context, job.project_id, "write")
    admitted_at = run.db.read_runtime_run_accepted_at(run.session_id, run.run_id)
    if admitted_at is None:
        raise ValueError("Domain job has no original admission clock")
    package = build_domain_package(run.context, run.db, job, admitted_at=admitted_at)
    metadata = package["metadata"]
    inputs = metadata.get("inputs", [])
    if not isinstance(inputs, list) or len(inputs) > 64:
        raise ValueError("Domain inputs exceed bound")
    for source in inputs:
        _read(run.context, run.db, job.project_id, source)
    outputs = package.get("outputs")
    if outputs is None:
        outputs = [{"name": "package.md", "mime": package.get("mime", "text/markdown"),
                    "content_bytes": package["content_bytes"]}]
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_OUTPUTS:
        raise ValueError("Domain output count exceeds bound")
    proposals, manifest_outputs, names, total = [], [], set(), 0
    for index, output in enumerate(outputs):
        name, data, mime = output["name"], output["content_bytes"], output["mime"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) or name in names:
            raise ValueError("Unique safe output basenames required")
        names.add(name)
        receipt = validate_artifact(data, mime)
        total += len(data)
        if total > MAX_JOB_BYTES:
            raise ValueError("Complete domain output bytes exceed bound")
        request = f"domain:{job.job_id}:{index}"
        if job.adapter in {"tutor", "educator"}:
            from hermes_cli.domain_education import prepare_education_package
            proposal = prepare_education_package(run, project_id=job.project_id, request_id=request, package=package)
        else:
            proposal = prepare_artifact(run, project_id=job.project_id, request_id=request,
                content_bytes=data, mime=mime,
                derived_from=[{"artifact_id": source["artifact_id"], "version": source["version"]} for source in inputs])
        proposals.append(proposal)
        ref = proposal.public_record()
        manifest_outputs.append({"name": name, "artifact_id": ref["artifact_id"], "version": ref["version"],
                                 "sha256": ref["sha256"], "mime": mime, "validation": receipt})
    manifest = {"schema_version": 1, "job_id": job.job_id, "adapter": job.adapter,
                "project_id": job.project_id, "request_sha256": hashlib.sha256(_json(job.to_record()).encode()).hexdigest(),
                "inputs": inputs, "transformations": metadata.get("transformations", []),
                "artifact_refs": manifest_outputs, "validator_manifest": metadata.get("validator_manifest", {}),
                "domain_metadata": metadata, "publication_atomic": False,
                "execution_scope": "local_deterministic", "external_effects": "none"}
    proposals.append(prepare_artifact(run, project_id=job.project_id,
        request_id=f"domain:{job.job_id}:manifest", content_bytes=(_json(manifest) + "\n").encode(),
        mime="application/json", derived_from=[{"artifact_id": ref["artifact_id"], "version": ref["version"]}
                                               for ref in manifest_outputs]))
    return tuple(proposals)


def publish_domain_job(run, proposals):
    """Require existing exact approvals; no approval is implied by transformation."""
    from hermes_cli.artifact_store import publish_artifact
    assert_artifact_dispatch(run)
    if not isinstance(proposals, tuple) or not 2 <= len(proposals) <= MAX_OUTPUTS + 1:
        raise ValueError("Prepared domain output bundle required")
    # Each effect is individually idempotent. A failure leaves earlier committed
    # members readable; no manifest-complete receipt is returned until the end.
    records = [publish_artifact(run, proposal) for proposal in proposals]
    return {"project_id": records[-1]["project_id"], "outputs": records[:-1], "manifest": records[-1], "state": "published",
            "publication_atomic": False, "external_production": "not_performed"}


def main(argv=None):
    """Validate an explicit local request; execution remains the owned RPC path."""
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Validate a bounded production request for runtime.domain.prepare")
    parser.add_argument("request")
    args = parser.parse_args(argv)
    with Path(args.request).open("rb") as handle:
        raw = handle.read(MAX_JOB_BYTES + 1)
    if len(raw) > MAX_JOB_BYTES:
        parser.error("request exceeds 4 MiB")
    job = DomainJob.from_record(json.loads(raw))
    print(_json({"request": job.to_record(), "status": "syntax_validated", "executed": False,
                 "next": "Use owned runtime.domain.prepare, then approve exact outputs with runtime.domain.publish"}))


if __name__ == "__main__":
    main()
