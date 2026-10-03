"""Per-point rollout policy. A promotion record is evidence, never permission."""
from dataclasses import dataclass
from agent.decisions.contracts import require, sha256, number, digest, label

MODES = frozenset({"off", "shadow", "advisory", "enforce"})


@dataclass(frozen=True)
class PointPolicy:
    mode: str = "off"
    threshold: float = .95
    timeout_seconds: float = .15
    gate_digest: str | None = None
    effect_thresholds: tuple[tuple[str, float], ...] = ()

    def __post_init__(self):
        require(isinstance(self.mode, str) and self.mode in MODES, "invalid_mode")
        number(self.threshold, .5, 1, "invalid_threshold")
        number(self.timeout_seconds, .001, 1, "invalid_timeout")
        if self.gate_digest is not None:
            sha256(self.gate_digest)
        require(isinstance(self.effect_thresholds, tuple) and len(self.effect_thresholds) <= 64, "invalid_thresholds")
        seen = set()
        for option, threshold in self.effect_thresholds:
            label(option)
            number(threshold, .5, 1, "invalid_threshold")
            require(option not in seen and option != "select", "invalid_thresholds")
            seen.add(option)

    def threshold_for(self, selected):
        return dict(self.effect_thresholds).get(selected, self.threshold)

    @property
    def policy_digest(self):
        return digest({"threshold": self.threshold, "timeout_seconds": self.timeout_seconds,
                       "effect_thresholds": dict(self.effect_thresholds)})


@dataclass(frozen=True)
class PointGate:
    point_id: str
    contract_digest: str
    model_digest: str
    calibration_digest: str
    policy_digest: str
    evidence_digest: str
    expires_at: float
    approved_effects: tuple[str, ...]

    def __post_init__(self):
        for value in (self.contract_digest, self.model_digest, self.calibration_digest,
                      self.policy_digest, self.evidence_digest):
            sha256(value)
        number(self.expires_at, 0, 253402300799)
        require(bool(self.approved_effects) and set(self.approved_effects) <= {"recommend"}, "unsupported_enforced_effect")

    @property
    def gate_digest(self):
        from dataclasses import asdict
        return digest(asdict(self))

    def permits(self, contract, bundle, policy, now):
        return (self.point_id == contract.point_id and self.contract_digest == contract.contract_digest
            and self.model_digest == bundle.model_digest and self.calibration_digest == bundle.calibration_digest
            and self.policy_digest == policy.policy_digest
            and self.gate_digest == policy.gate_digest and self.expires_at > now)
