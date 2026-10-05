"""Profile config selects a separate protocol; no enable flag qualifies a release."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from agent.decisions.contracts import DecisionError, ModelBundle
from agent.decisions.integration import _transport, parse_settings
from agent.decisions.laya_transport import LAYA_ENDPOINT, LayaHttpsTransport


def settings(mode="shadow"):
    bundle = asdict(ModelBundle("1" * 64, "2" * 64, "3" * 64))
    return {"schema_version": 2, "protocol": "laya_systemone", "bundle": bundle,
        "points": {"DP16": {"mode": mode}}, "destination": {
            "schema_version": 1, "endpoint": LAYA_ENDPOINT, "recipient_id": "laya",
            "secret_ref": "LAYA_API_KEY", **{"expected_" + key: value for key, value in bundle.items()}}}


def test_explicit_v2_construction_has_no_socket_or_secret_access(monkeypatch):
    monkeypatch.setattr("socket.socket", lambda *a, **k: pytest.fail("configuration opened a socket"))
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda *a, **k: pytest.fail("configuration loaded credentials"))
    raw = settings()
    parsed = parse_settings(raw)
    assert parsed[1]["DP16"].mode == "shadow"
    assert isinstance(_transport(raw), LayaHttpsTransport)
    assert parse_settings(settings("off")) is None
    assert parse_settings({"schema_version": 1, "points": {"DP16": {"mode": "off"}}}) is None


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(schema_version=True),
    lambda value: value.update(schema_version=3),
    lambda value: value.update(protocol="auto"),
    lambda value: value.update(node={}),
    lambda value: value.update(tls={}),
    lambda value: value.update(private_authorized=True),
    lambda value: value["points"]["DP16"].update(mode="enforce"),
    lambda value: value["points"].update(DP06={"mode": "shadow"}),
    lambda value: value["destination"].update(expected_service_digest="a" * 64),
    lambda value: value["destination"].update(endpoint="https://other.invalid/v1/systemone"),
    lambda value: value["destination"].update(allowed_classifications=["private"]),
    lambda value: value["destination"].update(secret_value="synthetic-do-not-accept-inline"),
    lambda value: value["destination"].update(synthetic_loopback=True),
])
def test_config_cannot_smuggle_a_destination_secret_privacy_grant_or_promotion(mutate):
    raw = deepcopy(settings())
    mutate(raw)
    with pytest.raises(DecisionError):
        parse_settings(raw)
