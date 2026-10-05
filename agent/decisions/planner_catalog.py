"""Bounded descriptive DP16 data, separate from dispatch and prompt instructions.

Only the host adapter assigns provenance/families. A schema's claimed source,
annotations, or description cannot certify authority or read-only behavior.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import unicodedata

from agent.decisions.contracts import canonical, digest, require, sha256

MAX_CATALOG_TOOLS = 1024
MAX_CATALOG_FAMILIES = 64
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = 512 * 1024
MAX_DESCRIPTION_CHARS = 768
MAX_FAMILY_DESCRIPTION_CHARS = 384
MAX_INPUT_HINTS = 8
MAX_INPUT_HINT_CHARS = 160
MAX_ID_CHARS = 512
SESSION_DESCRIPTION = (
    "Additional capabilities explicitly admitted to this session. Determine their "
    "purpose from the individual tool descriptions; no common effect is assumed."
)


def identifier(value):
    require(isinstance(value, str) and 0 < len(value) <= MAX_ID_CHARS
            and all(not unicodedata.category(char).startswith("C") for char in value),
            "invalid_catalog_id")
    return value


def catalog_alias(kind, identifier_value):
    """IDs are always aliased, so names such as 'none' cannot become controls."""
    require(kind in {"family", "tool"}, "invalid_catalog_alias_kind")
    identifier(identifier_value)
    return ("f_" if kind == "family" else "t_") + digest([kind, identifier_value])[:24]


def sanitize_description(value, *, limit=MAX_DESCRIPTION_CHARS):
    """Strip controls before forced redaction, then bound untrusted display data.

    No environment/credential reads: use the existing forced pattern redactor.
    The schema digest covers the untruncated original, including redacted text.
    """
    from agent.redact import REDACTION_UNAVAILABLE, redact_for_egress, redact_sensitive_text
    require(isinstance(value, str) and len(value) <= MAX_CATALOG_BYTES, "invalid_catalog_description")
    value = "".join(char for char in value if not unicodedata.category(char).startswith("C"))
    value = redact_for_egress(value)
    require(value != REDACTION_UNAVAILABLE, "catalog_redaction_unavailable")
    # Ordinary egress preserves one-use URLs. Classifier descriptions must not.
    value = redact_sensitive_text(value, force=True, redact_url_credentials=True)
    value = " ".join(value.split())
    return value[:limit]


@dataclass(frozen=True, slots=True)
class CatalogTool:
    tool_id: str
    alias: str
    family_id: str
    description: str
    source_digest: str
    schema_digest: str
    effect_summary: str
    input_hints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogFamily:
    family_id: str
    alias: str
    description: str
    tool_ids: tuple[str, ...]
    description_digest: str


def _input_hints(function):
    parameters = function.get("parameters", {})
    require(isinstance(parameters, dict), "invalid_catalog_schema")
    properties = parameters.get("properties", {})
    require(isinstance(properties, dict), "invalid_catalog_schema")
    hints = []
    for name, parameter in sorted(properties.items()):
        require(isinstance(name, str) and isinstance(parameter, dict), "invalid_catalog_schema")
        description = parameter.get("description")
        if isinstance(description, str) and description.strip():
            hint = sanitize_description(name + ": " + description, limit=MAX_INPUT_HINT_CHARS)
            if hint:
                hints.append(hint)
    return tuple(hints[:MAX_INPUT_HINTS])


def snapshot_fields(definitions, *, scope_digest, families, bridge_names, bridges,
                    policy_digest=None, tool_view_revision=None, family_descriptions=None,
                    sources=None, effects=None):
    """Pure constructor for a snapshot whose membership the caller authorized.

    Optional metadata maps are trusted host inputs, never read from schemas.
    They may name only admitted tools/families. Unknown effects remain unknown.
    """
    sha256(scope_digest)
    policy_digest = sha256(policy_digest or digest({"authorized_scope": scope_digest}))
    require(isinstance(definitions, (list, tuple)) and len(definitions) <= MAX_CATALOG_TOOLS,
            "catalog_tool_bound")
    frozen = canonical(definitions)
    require(len(frozen.encode()) <= MAX_CATALOG_BYTES, "catalog_byte_bound")
    definitions = json.loads(frozen)
    by_name = {}
    for item in definitions:
        require(isinstance(item, dict) and item.get("type") == "function"
                and isinstance(item.get("function"), dict), "invalid_catalog_schema")
        name = identifier(item["function"].get("name"))
        require(name not in by_name, "duplicate_catalog_tool")
        by_name[name] = item
    require(isinstance(families, dict) and len(families) <= MAX_CATALOG_FAMILIES, "catalog_family_bound")
    members, family_items = set(), []
    for family, names in sorted(families.items()):
        identifier(family)
        require(isinstance(names, (list, tuple)) and all(isinstance(name, str) for name in names),
                "invalid_family_tools")
        require(len(set(names)) == len(names), "duplicate_family_tool")
        require(set(names) <= set(by_name) - set(bridges) and not members.intersection(names),
                "invalid_family_tools")
        members.update(names)
        family_items.append((family, tuple(sorted(names))))
    require(members == set(by_name) - set(bridges), "catalog_membership_incomplete")
    require(isinstance(bridge_names, (list, tuple)) and len(set(bridge_names)) == len(bridge_names)
            and set(bridge_names) <= set(bridges), "invalid_bridge")
    family_descriptions, sources, effects = family_descriptions or {}, sources or {}, effects or {}
    require(set(family_descriptions) <= set(families) and set(sources) <= members and set(effects) <= members,
            "unexpected_catalog_metadata")
    tools, family_records = [], []
    for family, names in family_items:
        for name in names:
            function = by_name[name]["function"]
            description = sanitize_description(function.get("description", ""))
            require(bool(description), "missing_catalog_description")
            effect = effects.get(name, "unknown")
            require(effect in {"unknown", "read_only", "may_write"}, "invalid_catalog_effect")
            source = sources.get(name, digest({"kind": "authorized_snapshot", "scope": scope_digest,
                                                "family": family}))
            tools.append(CatalogTool(name, catalog_alias("tool", name), family, description, sha256(source),
                                     digest(by_name[name]), effect, _input_hints(function)))
        # Explicit mappings supplied by a host/fixture remain useful without a
        # built-in toolset description: summarize their actual member purposes.
        purpose = family_descriptions.get(family)
        if purpose is None:
            purpose = (SESSION_DESCRIPTION if family == "session" else
                       "Capabilities: " + "; ".join(tool.description for tool in tools if tool.family_id == family))
        purpose_digest = digest(purpose)
        purpose = sanitize_description(purpose, limit=MAX_FAMILY_DESCRIPTION_CHARS)
        require(bool(purpose), "missing_family_description")
        family_records.append(CatalogFamily(family, catalog_alias("family", family), purpose, names, purpose_digest))
    tools = tuple(sorted(tools, key=lambda tool: tool.tool_id))
    family_records = tuple(family_records)
    aliases = [entry.alias for entry in (*tools, *family_records)]
    require(len(set(aliases)) == len(aliases), "catalog_alias_collision")
    if tool_view_revision is None:
        tool_view_revision = digest({"scope": scope_digest, "policy": policy_digest,
                                     "tools": sorted(by_name), "families": family_items})
    sha256(tool_view_revision)
    values = {"scope_digest": scope_digest, "definitions_json": canonical([by_name[name] for name in sorted(by_name)]),
              "families": tuple(family_items), "bridge_names": tuple(sorted(bridge_names)),
              "tool_descriptors": tools, "family_descriptors": family_records,
              "policy_digest": policy_digest, "tool_view_revision": tool_view_revision}
    values["version"] = digest(version_material(values))
    require(len(canonical(descriptor_values(values)).encode()) <= MAX_DESCRIPTOR_BYTES, "catalog_descriptor_bound")
    return values


def validate_snapshot(values, *, bridges):
    """Keep direct dataclass construction as strict as the authorized factory."""
    require(isinstance(values["definitions_json"], str)
            and len(values["definitions_json"].encode()) <= MAX_CATALOG_BYTES, "catalog_byte_bound")
    try:
        definitions = json.loads(values["definitions_json"])
    except (ValueError, TypeError):
        require(False, "invalid_catalog")
    require(isinstance(definitions, list) and canonical(definitions) == values["definitions_json"]
            and len(definitions) <= MAX_CATALOG_TOOLS, "invalid_catalog")
    names = []
    for item in definitions:
        require(isinstance(item, dict) and item.get("type") == "function"
                and isinstance(item.get("function"), dict), "invalid_catalog_schema")
        names.append(identifier(item["function"].get("name")))
    require(len(names) == len(set(names)), "duplicate_catalog_tool")
    tools, families = values["tool_descriptors"], values["family_descriptors"]
    require(all(type(tool) is CatalogTool and type(tool.input_hints) is tuple for tool in tools)
            and all(type(family) is CatalogFamily and type(family.tool_ids) is tuple for family in families),
            "invalid_catalog_descriptors")
    require(len(families) <= MAX_CATALOG_FAMILIES
            and values["families"] == tuple((family.family_id, family.tool_ids) for family in families),
            "invalid_catalog_families")
    require(len(set(values["bridge_names"])) == len(values["bridge_names"])
            and set(values["bridge_names"]) <= set(bridges), "invalid_bridge")
    require(tuple(tool.tool_id for tool in tools) == tuple(sorted(set(names) - set(bridges))),
            "catalog_membership_incomplete")
    members = [(family.family_id, name) for family in families for name in family.tool_ids]
    require(sorted(members) == sorted((tool.family_id, tool.tool_id) for tool in tools), "invalid_family_tools")
    aliases = [item.alias for item in (*tools, *families)]
    require(len(set(aliases)) == len(aliases), "catalog_alias_collision")
    require(len({family.family_id for family in families}) == len(families), "invalid_catalog_families")
    for item, kind, key in [(tool, "tool", tool.tool_id) for tool in tools] + [
            (family, "family", family.family_id) for family in families]:
        require(item.alias == catalog_alias(kind, key), "invalid_catalog_alias")
        limit = MAX_DESCRIPTION_CHARS if kind == "tool" else MAX_FAMILY_DESCRIPTION_CHARS
        require(isinstance(item.description, str) and 0 < len(item.description) <= limit
                and all(not unicodedata.category(char).startswith("C") for char in item.description),
                "invalid_catalog_description")
    for tool in tools:
        sha256(tool.source_digest)
        sha256(tool.schema_digest)
        require(tool.effect_summary in {"unknown", "read_only", "may_write"}
                and len(tool.input_hints) <= MAX_INPUT_HINTS
                and all(isinstance(hint, str) and len(hint) <= MAX_INPUT_HINT_CHARS for hint in tool.input_hints),
                "invalid_catalog_descriptors")
    for family in families:
        sha256(family.description_digest)
    require(len(canonical(descriptor_values(values)).encode()) <= MAX_DESCRIPTOR_BYTES, "catalog_descriptor_bound")


def version_material(values):
    return {**{key: values[key] for key in ("scope_digest", "definitions_json", "families", "bridge_names",
                                           "policy_digest", "tool_view_revision")},
            "catalog_contract": "dp16_catalog_v2",
            "tool_descriptors": [asdict(item) for item in values["tool_descriptors"]],
            "family_descriptors": [asdict(item) for item in values["family_descriptors"]]}


def descriptor_values(values):
    families = values["family_descriptors"]
    tools = values["tool_descriptors"]
    tool_aliases = {tool.tool_id: tool.alias for tool in tools}
    family_aliases = {family.family_id: family.alias for family in families}
    return {"version": values["version"], "scope_digest": values["scope_digest"],
            "policy_digest": values["policy_digest"], "tool_view_revision": values["tool_view_revision"],
            "families": [{"id": family.alias, "description": family.description,
                          "tools": [tool_aliases[name] for name in family.tool_ids]} for family in families],
            "tools": [{"id": tool.alias, "family": family_aliases[tool.family_id],
                       "description": tool.description, "source_digest": tool.source_digest,
                       "schema_digest": tool.schema_digest, "effect_summary": tool.effect_summary,
                       "input_hints": list(tool.input_hints)} for tool in tools],
            "bridges": list(values["bridge_names"])}


def host_metadata(admitted, context, *, registry, scope_digest):
    """Assign source/family only after live admission; do not call tool handlers."""
    from toolsets import TOOLSETS
    names = {item["function"]["name"] for item in admitted}
    metadata = {item.name: item for item in registry.catalog_metadata() if item.name in names}
    families, descriptions, sources, effects = {}, {}, {}, {}
    for item in admitted:
        name = item["function"]["name"]
        entry, registered = registry.get_entry(name), metadata.get(name)
        require((entry is None) == (registered is None), "catalog_registration_changed")
        toolset = registered.toolset if registered is not None else "session"
        purpose = (TOOLSETS.get(toolset) or {}).get("description")
        # Unknown plugin/MCP groups have no owner-maintained family purpose.
        # Explicit session fallback keeps individual descriptions, never guesses
        # a capability category from a tool or server-name prefix.
        family = toolset if isinstance(purpose, str) and purpose.strip() else "session"
        families.setdefault(family, []).append(name)
        descriptions[family] = purpose if family != "session" else SESSION_DESCRIPTION
        target = getattr(entry.handler, "_agent_mcp_target", None) if entry is not None else None
        source = {"kind": "registered" if registered is not None else "explicit_session",
                  "scope": scope_digest, "policy": context.policy.digest, "toolset": toolset,
                  "handler_module": getattr(entry.handler, "__module__", "") if entry is not None else "",
                  "handler_name": getattr(entry.handler, "__qualname__", "") if entry is not None else "",
                  "registered_schema": registered.schema_json if registered is not None else None}
        if isinstance(target, tuple) and len(target) == 2:
            server, operation = target
            grant = context.policy.mcp_policies.get(server, {})
            source["mcp_target"] = target
            source["operator_schema_digest"] = grant.get("schema_digests", {}).get(operation)
            if operation in grant.get("read_only_tools", ()):
                effects[name] = "read_only"
        sources[name] = digest(source)
    return {"families": families, "family_descriptions": descriptions, "sources": sources, "effects": effects}
