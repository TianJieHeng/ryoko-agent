"""Metadata-only health validation. Hardware assertions require operator evidence."""
from agent.decisions.contracts import number, require


def validate_health(raw, manifest):
    require(isinstance(raw, dict) and set(raw) == {"schema_version", "service_digest", "registry_digest",
        "model_digest", "calibration_digest", "queue_depth", "max_queue", "memory_used_bytes",
        "memory_limit_bytes", "uptime_seconds", "ready", "hardware_verified"}, "invalid_health_schema")
    require(type(raw["schema_version"]) is int and raw["schema_version"] == 1, "invalid_health_schema")
    for key in ("service_digest", "registry_digest", "model_digest", "calibration_digest", "max_queue"):
        require(raw[key] == getattr(manifest, key), "health_bundle_mismatch")
    for key in ("queue_depth", "max_queue", "memory_used_bytes", "memory_limit_bytes"):
        require(type(raw[key]) is int, "invalid_health_resource")
    number(raw["queue_depth"], 0, manifest.max_queue, "invalid_health_resource")
    number(raw["memory_limit_bytes"], 1, 2**50, "invalid_health_resource")
    number(raw["memory_used_bytes"], 0, raw["memory_limit_bytes"], "invalid_health_resource")
    number(raw["uptime_seconds"], 0, 10**10, "invalid_health_resource")
    require(type(raw["ready"]) is bool and type(raw["hardware_verified"]) is bool, "invalid_health_schema")
    return dict(raw)
