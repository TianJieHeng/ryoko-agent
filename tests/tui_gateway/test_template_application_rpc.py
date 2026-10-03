"""Owned canonical template application, exact approval, locks and old-version pin."""
import hashlib

import pytest
from tests.tui_gateway.test_artifact_rpc import artifacts, result, denied, publish, download  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


def saved(rpc, project, version=1, parent=None):
    baseline = publish(rpc, {"project_id": project, "command_id": f"base-{version}", "request_id": f"base-{version}",
                             "content": "# Example\nINCIDENTAL_NAME at Example Corp\n"})
    return result(rpc.call("runtime.template.create", project_id=project, template_id="canonical-brief", version=version,
        parent_version=parent, baseline_ref={key: baseline[key] for key in ("artifact_id", "version")},
        structure=["# ${slot.topic}", "## Summary\n${slot.summary}", "## Policy\nKeep sources visible."],
        style={"section_spacing": "rule", "tone": "plain"}, assets=[],
        slots=[{"name": key, "purpose": key, "required": True} for key in ("topic", "summary")],
        exclusions=["INCIDENTAL_NAME", "Example Corp"]))["template"]


def pin(row):
    return {"store": "artifact_templates", **{key: row[key] for key in ("template_id", "version", "sha256")}}


def test_preview_accept_three_topics_and_lock_revisions(artifacts):
    project = artifacts.project()["id"]
    row = saved(artifacts, project)
    for i, topic in enumerate(("Botany", "Robotics", "Accessible cafés")):
        request = {"project_id": project, "template_ref": pin(row), "slot_values": {"topic": topic, "summary": "Fresh topic facts"},
                   "locked_sections": ["Policy"]}
        preview = result(artifacts.call("runtime.template.preview", **request))
        assert preview["content"].startswith("# " + topic) and "INCIDENTAL_NAME" not in preview["content"]
        assert preview["advisory_style_keys"] == ["tone"] and preview["assets_mode"] == "lineage_only"
        request.update(command_id=f"apply-{i}", request_id=f"apply-{i}")
        prepared = result(artifacts.call("runtime.template.prepare", **request))
        assert prepared["preview"] == preview
        proposal = prepared["proposal"]
        assert proposal["sha256"] == preview["sha256"]
        denied(artifacts.call("runtime.template.publish", **request, approval_id=proposal["approval_id"],
                             approval_digest="0" * 64), "approval_mismatch")
        output = result(artifacts.call("runtime.template.publish", **request,
            **{key: proposal[key] for key in ("approval_id", "approval_digest")}))
        assert download(artifacts, project, output["artifact_id"]) == preview["content"].encode()
        denied(artifacts.call("runtime.artifact.edit.prepare", project_id=project, command_id=f"locked-{i}", request_id=f"locked-{i}",
            artifact_id=output["artifact_id"], parent_version=1, edits=[{"anchor": "Policy", "expected_sha256":
            hashlib.sha256(b"## Policy\nKeep sources visible.\n").hexdigest(), "replacement": "## Policy\nChanged\n"}]))
        result(artifacts.call("runtime.artifact.cancel", command_id=f"locked-{i}"))


def test_pinned_version_exclusion_wrong_project_and_actor_isolation(artifacts):
    project = artifacts.project()["id"]
    other = artifacts.project()["id"]
    first = saved(artifacts, project)
    request = {"project_id": project, "template_ref": pin(first), "slot_values": {"topic": "New", "summary": "Current"}}
    before = result(artifacts.call("runtime.template.preview", **request))
    saved(artifacts, project, 2, 1)
    assert result(artifacts.call("runtime.template.preview", **request)) == before
    denied(artifacts.call("runtime.template.preview", **{**request, "project_id": other}), "identity_mismatch")
    denied(artifacts.call("runtime.template.preview", "b", **request))
    assert result(artifacts.call("runtime.template.preview", **request)) == before
    denied(artifacts.call("runtime.template.preview", **{**request, "slot_values": {"topic": "INCIDENTAL_NAME", "summary": "Leak"}}),
           "template_exclusion_violation")
    denied(artifacts.call("runtime.template.preview", **{**request, "template_ref": {**pin(first), "sha256": "0" * 64}}),
           "template_digest_mismatch")


def test_canonical_workflow_bridge_applies_same_saved_bytes_and_keeps_old_pin(artifacts):
    import json
    from tests.agent.test_workflow_contract import workflow_record, object_schema
    from tests.tui_gateway.test_workflows_rpc import evaluate, decision, ready_mission, run_request
    project = artifacts.project()["id"]
    template = saved(artifacts, project)
    definition = workflow_record()
    definition.update(project_id=project, template_ref=pin(template),
        input_schema=object_schema({key: {"type": "string"} for key in ("topic", "summary")}, ["topic", "summary"]),
        output_schema={"required_sections": ["Summary", "Policy"], "min_bytes": 1})
    definition["steps"][0]["parameters"] = {"template": "__canonical_template__"}
    definition["provenance"]["private_derived"] = True
    row = result(artifacts.call("runtime.workflow.create", command_id="canonical-workflow", definition_json=json.dumps(definition)))["workflow"]
    assert row["state"] == "draft"
    cases = []
    for index, topic in enumerate(("Solar", "Wind", "Hydro", "Geothermal")):
        parameters = {"topic": topic, "summary": "Current facts for " + topic}
        preview = result(artifacts.call("runtime.template.preview", project_id=project, template_ref=pin(template), slot_values=parameters))
        baseline = publish(artifacts, {"project_id": project, "command_id": f"workflow-base-{index}",
                                     "request_id": f"workflow-base-{index}", "content": preview["content"]})
        cases.append({"case_id": str(index), "split": "tuning" if index < 2 else "held_out", "parameters": parameters,
                      "expected_sha256": baseline["sha256"], "generalist_ref": {key: baseline[key] for key in ("artifact_id", "version", "sha256")}})
    row = decision(artifacts, evaluate(artifacts, row, cases)["workflow"])["workflow"]
    saved(artifacts, project, 2, 1)
    mission = ready_mission(artifacts, project)
    request = run_request(row, mission, "unused", "canonical-run")
    request["parameters_json"] = json.dumps({"topic": "Tidal", "summary": "Fresh tidal facts"})
    prepared = result(artifacts.call("runtime.workflow.run.prepare", **request))
    published = result(artifacts.call("runtime.workflow.run.publish", **request,
        approvals=[{key: item[key] for key in ("approval_id", "approval_digest")} for item in prepared["proposals"]]))
    content = download(artifacts, project, published["outputs"][0]["artifact_id"])
    assert b"# Tidal" in content and b"Fresh tidal facts" in content and b"INCIDENTAL_NAME" not in content
    assert result(artifacts.call("runtime.workflow.get", project_id=project, workflow_id=row["workflow_id"], version=1))["workflow"]["sha256"] == row["sha256"]
    ambiguous = {**definition, "workflow_id": "ambiguous", "template_ref": {key: value for key, value in pin(template).items() if key != "store"}}
    denied(artifacts.call("runtime.workflow.create", command_id="no-store-fallback", definition_json=json.dumps(ambiguous)), "workflow_template_mismatch")


def test_legacy_style_only_template_cannot_silently_render_unrelated_text(artifacts):
    import json
    from tests.agent.test_workflow_contract import workflow_record
    from tests.tui_gateway.test_workflows_rpc import baselines
    project = artifacts.project()["id"]
    legacy = result(artifacts.call("runtime.workflow.template.create", command_id="legacy-style",
        definition_json=json.dumps({"template_id": "legacy", "version": 1, "project_id": project,
                                    "style": "Formal", "sections": ["Greeting"], "predecessor": None})))
    definition = workflow_record()
    definition.update(project_id=project, template_ref={key: legacy[key] for key in ("template_id", "version", "sha256")})
    workflow = result(artifacts.call("runtime.workflow.create", command_id="legacy-workflow", definition_json=json.dumps(definition)))["workflow"]
    cases = baselines(artifacts, project)
    denied(artifacts.call("runtime.workflow.evaluate", project_id=project, command_id="legacy-evaluate", workflow_id=workflow["workflow_id"],
        version=1, expected_revision=workflow["revision"], cases_json=json.dumps(cases)), "workflow_legacy_template_not_applicable")
