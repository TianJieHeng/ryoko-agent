"""Canonical template renderer applies authored structure, never baseline bodies."""
import pytest
from hermes_cli.template_application import render_template, template_digest
from hermes_state_runtime import RuntimeStoreError


def template():
    return {"template_id": "brief", "version": 1, "project_id": "project", "baseline_ref": {"artifact_id": "original", "version": 1},
            "structure": ["# ${slot.topic}", "## Summary\n${slot.summary}", "## Policy\nKeep sources visible."],
            "style": {"tone": "plain", "section_spacing": "rule"}, "assets": [],
            "slots": [{"name": key, "purpose": key, "required": True} for key in ("topic", "summary")],
            "exclusions": ["Example Corp", "INCIDENTAL_NAME"], "parent_version": None}


@pytest.mark.parametrize("topic", ["Ocean wildlife", "Lunar navigation", "Café logistics"])
def test_three_varied_topics_preserve_structure_format_and_no_incidental_names(topic):
    content, advisory = render_template(template(), {"topic": topic, "summary": "Fresh facts for " + topic})
    assert content.startswith("# " + topic) and "\n\n---\n\n## Summary\n" in content
    assert "## Policy\nKeep sources visible." in content and advisory == ["tone"]
    assert "Example Corp" not in content and "INCIDENTAL_NAME" not in content


def test_literal_nonrecursive_slots_and_required_unused_excluded_values():
    assert "${slot.secret}" in render_template(template(), {"topic": "${slot.secret}", "summary": "Literal"})[0]
    for values, code in [({"summary": "Missing"}, "template_slot_missing"),
                         ({"topic": "EXAMPLE CORP", "summary": "No leak"}, "template_exclusion_violation"),
                         ({"topic": "T", "summary": "S", "secret": "No"}, "template_slots_invalid")]:
        with pytest.raises(RuntimeStoreError) as error:
            render_template(template(), values)
        assert error.value.code == code


def test_owner_metadata_is_not_part_of_content_pin_but_all_reusable_fields_are():
    row = template()
    original = template_digest(row)
    assert template_digest({**row, "owner_actor": {"agent_id": "a"}}) == original
    assert template_digest({**row, "exclusions": []}) != original
    assert template_digest({**row, "structure": ["Changed"]}) != original
