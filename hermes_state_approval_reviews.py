"""Bounded immutable review bytes, separate from redacted approval listings.

Only trusted action producers supply these records. The complete exact action is
validated against the approval binding before storage. Secret-bearing material is
withheld, never silently replaced by a misleading redacted approval preview.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re

from hermes_state_effects import _check, _json, effect_digest, EffectStoreError

_MAX_REVIEW_BYTES = 131072
_SECRET_FIELD = re.compile(r"(?:password|passwd|secret|credential|authorization|cookie|api[_-]?key|access[_-]?token|refresh[_-]?token)", re.I)


def _has_secret_field(value):
    if isinstance(value, dict):
        return any(_SECRET_FIELD.search(key) or _has_secret_field(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_secret_field(item) for item in value)
    return False


def prepare_review(review, binding):
    if review is None:
        return None
    _check(isinstance(review, dict) and set(review) == {"action", "content"},
           "invalid_approval_review", "Exact producer action and content are required")
    action, content = review["action"], review["content"]
    fields = {"name", "arguments", "operation_class", "resource_roots", "destination", "destination_purpose", "contract_digest"}
    _check(isinstance(action, dict) and set(action) == fields and isinstance(action["arguments"], dict)
           and all(isinstance(action[key], str) for key in ("name", "operation_class", "destination", "destination_purpose", "contract_digest"))
           and isinstance(action["resource_roots"], list) and all(isinstance(root, str) for root in action["resource_roots"]),
           "invalid_approval_review", "Exact action schema required")
    _check(effect_digest(action) == binding["action_digest"], "approval_review_mismatch", "Action digest differs")
    encoded_input = _json(action["arguments"], maximum=65536)
    if action["operation_class"] == "project_artifact_publish":
        descriptor = action["arguments"].get("descriptor")
        _check(isinstance(descriptor, dict) and {"artifact_id", "version", "sha256", "mime", "size"} <= set(descriptor),
               "invalid_approval_review", "Immutable artifact descriptor is required")
        _check(effect_digest(descriptor) == binding["input_digest"]
               and binding["target_ref"] == f"artifact:{descriptor['artifact_id']}:{descriptor['version']}",
               "approval_review_mismatch", "Artifact revision differs")
        if content is not None:
            _check(isinstance(content, dict) and set(content) == {"encoding", "data", "sha256", "mime"}
                   and content["encoding"] == "base64", "invalid_approval_review", "Exact content bytes required")
            try:
                data = base64.b64decode(content["data"], validate=True)
            except (ValueError, TypeError) as exc:
                raise EffectStoreError("invalid_approval_review", "Invalid content encoding") from exc
            _check(len(data) <= 65536 and hashlib.sha256(data).hexdigest() == content["sha256"] == descriptor["sha256"]
                   and content["mime"] == descriptor["mime"] and len(data) == descriptor["size"],
                   "approval_review_mismatch", "Content digest differs")
    else:
        _check(content is None and hashlib.sha256(encoded_input.encode()).hexdigest() == binding["input_digest"]
               and effect_digest({"destination": action["destination"], "purpose": action["destination_purpose"],
                                  "resource_roots": action["resource_roots"]}) == binding["target_ref"],
               "approval_review_mismatch", "Input or destination digest differs")
    encoded = _json(review, maximum=262144)
    if len(encoded.encode()) > _MAX_REVIEW_BYTES:
        return {"reviewable": False, "unavailable_reason": "review_size_limit", "review": None}
    from agent.redact import redact_sensitive_text, redact_registered_vault_values
    # Transport encoding is not semantic content: base64-encoded JSON starts
    # with eyJ and resembles a JWT. Scan the exact decoded bytes below plus
    # all action/content metadata; keep the immutable stored review unchanged.
    scan_review = {"action": action, "content": None if content is None else
                   {key: value for key, value in content.items() if key != "data"}}
    text = _json(scan_review, maximum=262144) + _json({key: binding[key] for key in ("input_revision", "artifact_revision", "target_ref")})
    if content is not None:
        try:
            text += data.decode("utf-8")
        except UnicodeDecodeError:
            # Opaque binary payloads cannot pass the text credential boundary.
            return {"reviewable": False, "unavailable_reason": "opaque_content", "review": None}
    if (_has_secret_field(action) or redact_sensitive_text(text, force=True, redact_url_credentials=True) != text
            or redact_registered_vault_values(text) != text):
        return {"reviewable": False, "unavailable_reason": "sensitive_content", "review": None}
    if action["operation_class"] == "project_artifact_publish" and content is None:
        return {"reviewable": False, "unavailable_reason": "content_not_retained", "review": None}
    return {"reviewable": True, "unavailable_reason": None, "review": review}


def review_digest(prepared):
    return hashlib.sha256(_json(prepared, maximum=_MAX_REVIEW_BYTES).encode()).hexdigest()


def write_review_on_conn(conn, approval_id, prepared):
    if prepared is not None:
        encoded = _json(prepared, maximum=_MAX_REVIEW_BYTES)
        conn.execute("INSERT INTO runtime_approval_reviews VALUES(?,?,?)",
                     (approval_id, encoded, hashlib.sha256(encoded.encode()).hexdigest()))


def get_approval_detail(db, approval_id, actor):
    from hermes_state_effects import _actor
    actor = _actor(actor)
    with db._runtime_read() as conn:
        row = db._effect_approval_on_conn(conn, approval_id, actor)
        approval = db._approval_projection_on_conn(conn, row)
        detail = conn.execute("SELECT review_json,review_digest FROM runtime_approval_reviews WHERE approval_id=?",
                              (approval_id,)).fetchone()
        if detail is None:
            return approval, {"reviewable": False, "unavailable_reason": "review_not_retained", "review": None,
                              "review_digest": None}, _decision_on_conn(conn, approval)
        _check(hashlib.sha256(detail["review_json"].encode()).hexdigest() == detail["review_digest"],
               "approval_review_mismatch", "Retained review digest differs")
        prepared = json.loads(detail["review_json"])
        expected = effect_digest({"approval_id": approval_id, "actor": actor, "binding": approval["binding"],
                                  "expires_at": row["expires_at"], "review_digest": detail["review_digest"]})
        _check(expected == row["approval_digest"], "approval_review_mismatch", "Approval review binding differs")
        # Recheck secrets at read time, including values newly registered in this profile.
        if prepared["review"] is not None:
            prepared = prepare_review(prepared["review"], approval["binding"])
        return approval, {**prepared, "review_digest": detail["review_digest"]}, _decision_on_conn(conn, approval)


def _decision_on_conn(conn, approval):
    row = conn.execute("SELECT choice,resolved_at FROM runtime_approval_decisions WHERE approval_id=?",
                       (approval["approval_id"],)).fetchone()
    choice = row["choice"] if row else ("once" if approval["status"] in {"approved", "consumed"}
                                       else "deny" if approval["status"] == "denied" else None)
    return {"choice": choice, "resolved_at": row["resolved_at"] if row else approval["resolved_at"],
            "consumed_at": approval["consumed_at"]}
