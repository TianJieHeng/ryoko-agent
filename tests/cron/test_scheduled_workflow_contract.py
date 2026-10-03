"""The schedule grant names one finite local draft destination and input policy."""
from copy import deepcopy
import pytest
from cron.durable_contract import validate_definition
from cron.durable_workflow_contract import grant_target
from tests.cron.test_durable_schedule_contract import definition


def workflow_schedule():
    record = definition()
    record.update(kind="workflow_draft", specification={"workflow_ref": {"workflow_id": "brief", "version": 1, "sha256": "a" * 64},
        "parameters": {"topic": "Status"}, "source_bindings": [{"parameter": "source", "artifact_id": "retained-notes"}],
        "destination": {"kind": "project_artifact_drafts", "project_id": record["project_id"]}})
    return record


def test_workflow_version_parameters_sources_project_destination_and_budget_change_grant_target():
    record = validate_definition(workflow_schedule())
    original = grant_target(record)
    for path, replacement in [(("specification", "workflow_ref", "version"), 2),
        (("specification", "workflow_ref", "sha256"), "b" * 64),
        (("specification", "parameters", "topic"), "Changed"),
        (("specification", "destination", "project_id"), "other"),
        (("project_id",), "other"), (("budget", "max_bytes"), 10001)]:
        changed = deepcopy(record)
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = replacement
        assert grant_target(changed) != original
    changed = deepcopy(record)
    changed["specification"]["source_bindings"][0]["artifact_id"] = "other-source"
    assert grant_target(changed) != original


@pytest.mark.parametrize("mutation", [
    lambda spec: spec["destination"].update(kind="external_send"),
    lambda spec: spec.update(script="echo unauthorized"),
    lambda spec: spec["source_bindings"].append(dict(spec["source_bindings"][0])),
    lambda spec: spec["parameters"].update(source="collides"),
    lambda spec: spec["workflow_ref"].pop("sha256"),
    lambda spec: spec.update(source_bindings=[{"parameter": "source", "artifact_id": "file:///private"}]),
])
def test_unknown_execution_input_and_destination_shapes_are_rejected(mutation):
    record = workflow_schedule()
    mutation(record["specification"])
    with pytest.raises(ValueError):
        validate_definition(record)
