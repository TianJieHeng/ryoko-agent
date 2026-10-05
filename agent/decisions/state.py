"""Compact state packets. Text is data; no transcript or automatic credential export."""
from agent.decisions.contracts import StatePacket, canonical, require
from agent.decisions.registry import contract_for


def build_state(point_id, values, *, scope_digest, classification="private", contract_version=1):
    contract = contract_for(point_id, contract_version)
    require(isinstance(values, dict) and set(values) <= set(contract.state_fields), "invalid_state_fields")
    def bounded(value, depth=0):
        require(depth <= (8 if contract.version == 2 else 3), "state_too_deep")
        if type(value) is str:
            require(len(value) <= 4096 and "\0" not in value, "state_text_bound")
        elif type(value) in (bool, int, float) or value is None:
            canonical(value)
        elif type(value) is list:
            require(len(value) <= 64, "state_list_bound")
            for item in value:
                bounded(item, depth + 1)
        elif type(value) is dict:
            require(len(value) <= 64 and all(isinstance(key, str) and len(key) <= 96 for key in value), "state_map_bound")
            for item in value.values():
                bounded(item, depth + 1)
        else:
            require(False, "invalid_state_value")
    bounded(values)
    state_json = canonical(values)
    if contract.version == 2:
        require(len(state_json.encode("utf-8")) <= 12288, "state_too_large")
    return StatePacket(state_json, scope_digest, classification)
