"""Opt-in synthetic node benchmark. No installation, model load or training.

Run separately against each pinned PyTorch and ONNX/TensorRT service; compare
reports only when the exact model, calibration, registry and cases match.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.decisions.client import DecisionClient
from agent.decisions.contracts import ModelBundle, digest, require
from agent.decisions.health import validate_health
from agent.decisions.policy import PointPolicy
from agent.decisions.state import build_state
from agent.decisions.transport import LanTransport, NodeManifest

CASES = ("Say hello without tools", "Find the synthetic fixture file", "An unclear synthetic request")


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def benchmark(transport, manifest, *, backend, rounds=5, warmup=1, hardware_evidence_digest=None):
    require(backend in {"pytorch", "onnx", "tensorrt_fp16", "synthetic_fixture"}, "invalid_backend")
    require(type(rounds) is int and 1 <= rounds <= 1000 and type(warmup) is int and 0 <= warmup <= 100, "invalid_rounds")
    if hardware_evidence_digest is not None:
        from agent.decisions.contracts import sha256
        sha256(hardware_evidence_digest)
    health = validate_health(transport.health(), manifest)
    client = DecisionClient(bundle=ModelBundle(manifest.model_digest, manifest.calibration_digest, manifest.service_digest),
                            transport=transport, policies={"DP16": PointPolicy("shadow", timeout_seconds=1)})
    samples, batches, predictions, failures = [], [], [], 0
    for iteration in range(warmup + rounds):
        batch_start = time.perf_counter()
        batch = []
        for text in CASES:
            packet = build_state("DP16", {"request": text}, scope_digest=digest({"purpose": "synthetic_hardware_benchmark"}),
                                 classification="synthetic")
            start = time.perf_counter()
            outcome = client.decide("DP16", packet, 1, time.time() + 1)
            elapsed = (time.perf_counter() - start) * 1000
            if iteration >= warmup:
                samples.append(elapsed)
                failures += outcome.fallback != "shadow_observation"
                batch.append(outcome.receipt["distribution"] if outcome.receipt else None)
        if iteration >= warmup:
            batches.append((time.perf_counter() - batch_start) * 1000)
            predictions.append(batch)
    return {"schema_version": 1, "backend": backend, "synthetic": True,
        "hardware_qualified": False, "hardware_evidence_digest": hardware_evidence_digest,
        "client_machine": platform.machine(), "client_system": platform.system(),
        "node_hardware": "unverified_by_this_runner", "model_digest": manifest.model_digest,
        "calibration_digest": manifest.calibration_digest, "registry_digest": manifest.registry_digest,
        "service_digest": manifest.service_digest, "cases_digest": digest(CASES), "health": health,
        "samples": len(samples), "warmup_rounds_excluded": warmup, "failure_count": failures,
        "end_to_end_ms": {"p50": percentile(samples, .5), "p95": percentile(samples, .95), "max": max(samples)},
        "sequential_batch_ms": {"size": len(CASES), "p50": percentile(batches, .5), "p95": percentile(batches, .95)},
        "raw_end_to_end_ms": samples, "raw_sequential_batch_ms": batches, "predictions": predictions,
        "promotion_eligible": False}


def compare(first, second, *, tolerance=1e-4):
    require(0 <= tolerance <= .1, "invalid_tolerance")
    for key in ("model_digest", "calibration_digest", "registry_digest", "cases_digest", "samples"):
        require(first[key] == second[key], "benchmark_bundle_mismatch")
    parity = first["failure_count"] == second["failure_count"] == 0
    for left_batch, right_batch in zip(first["predictions"], second["predictions"], strict=True):
        for left, right in zip(left_batch, right_batch, strict=True):
            if left is None or right is None or set(left) != set(right):
                parity = False
            elif (max(left, key=left.get) != max(right, key=right.get)
                  or any(abs(left[key] - right[key]) > tolerance for key in left)):
                parity = False
    return {"answer_parity": parity, "distribution_tolerance": tolerance,
            "first_backend": first["backend"], "second_backend": second["backend"],
            "first_p95_ms": first["end_to_end_ms"]["p95"], "second_p95_ms": second["end_to_end_ms"]["p95"],
            "hardware_qualified": False, "promotion_eligible": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--tls", type=Path, required=True, help="JSON paths to existing CA/client certificate/key")
    run.add_argument("--backend", required=True, choices=("pytorch", "onnx", "tensorrt_fp16"))
    run.add_argument("--rounds", type=int, default=5)
    run.add_argument("--hardware-evidence-digest")
    comp = sub.add_parser("compare")
    comp.add_argument("first", type=Path)
    comp.add_argument("second", type=Path)
    args = parser.parse_args()
    def read(path):
        require(path.stat().st_size <= 1024 * 1024, "benchmark_input_too_large")
        return json.loads(path.read_text(encoding="utf-8"))
    if args.action == "compare":
        report = compare(read(args.first), read(args.second))
    else:
        manifest = NodeManifest.from_record(read(args.manifest))
        report = benchmark(LanTransport(manifest, **read(args.tls)), manifest, backend=args.backend,
                           rounds=args.rounds, hardware_evidence_digest=args.hardware_evidence_digest)
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
