"""Compact state packets. Text is data; no transcript or automatic credential export."""
from agent.decisions.contracts import StatePacket, canonical, require
from agent.decisions.registry import contract_for


def build_state(point_id, values, *, scope_digest, classification="private"):
    contract = contract_for(point_id)
    require(isinstance(values, dict) and set(values) <= set(contract.state_fields), "invalid_state_fields")
    def bounded(value, depth=0):
        require(depth <= 3, "state_too_deep")
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
    return StatePacket(canonical(values), scope_digest, classification)
