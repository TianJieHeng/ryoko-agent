"""Bounded prompt scheduling; authority never grants arbitrary external effects."""
from cron.durable_contract import exact, identifier, require, instant


def validate_command_specification(value):
    exact(value, "prompt session_id authority_description")
    identifier(value["session_id"])
    require(isinstance(value["prompt"], str) and value["prompt"].strip() and len(value["prompt"]) <= 16000,
            "Bounded conversation prompt required")
    require(isinstance(value["authority_description"], str) and value["authority_description"].strip() and
            len(value["authority_description"]) <= 2000, "Standing authority description required")


def validate_import(value):
    exact(value, "authority source_id source_state unresolved_occurrences occurrences")
    identifier(value["source_id"])
    require(value["authority"] == "dots_runner" and isinstance(value["source_state"], str) and value["source_state"] in {"paused", "retired"}
            and value["unresolved_occurrences"] == [],
            "Stop legacy admission and reconcile active claims before import", "schedule_cutover_blocked")
    require(isinstance(value["occurrences"], list) and len(value["occurrences"]) <= 100,
            "Bounded retained legacy history required")
    ids = set()
    for item in value["occurrences"]:
        exact(item, "source_occurrence_id due_at state")
        identifier(item["source_occurrence_id"])
        instant(item["due_at"])
        require(isinstance(item["state"], str) and item["state"] in {"completed", "failed", "cancelled", "skipped"},
                "Reconcile legacy claims before importing", "schedule_cutover_blocked")
        require(item["source_occurrence_id"] not in ids, "Duplicate legacy occurrence")
        ids.add(item["source_occurrence_id"])
