# LAYA systemone operator runbook

The code path is implemented; real-host acceptance and release qualification are
still open. Use [readiness](laya-integration-readiness.json), [ADR 013](laya-integration-contract.md)
and the [build plan](../../LAYA_Integration_BuildPlan.md). This is separate from
the existing [pinned mutual-TLS LAN protocol](decision-node-runbook.md).

## Default and allowed destination

Production defaults off. Version 2 profile configuration selects only DP16 and
`laya_systemone`. The exact destination is
`https://laya.ryoko.okinawa/v1/systemone`; only POST is permitted. The separately
authorized `https://laya.ryoko.okinawa/health` is GET without a credential.
Health is liveness only; it does not establish authentication, inference quality,
loaded checkpoint identity or the full three-stage latency.

The destination manifest has schema version 1 and these fields:

- `endpoint`: the exact inference URL above
- `recipient_id`: the exact configured owner recipient grant ID
- `secret_ref`: `LAYA_API_KEY` only
- `expected_model_digest`, `expected_calibration_digest`, `expected_service_digest`:
  the complete expected bundle's SHA-256 values
- Optional `allowed_classifications`: `synthetic` and/or `public`; `private` is rejected
- Optional `max_request_bytes` and `max_response_bytes`: positive integers at most 32768
- Optional `model_alias`: an exact expected response alias; an alias is not artifact attestation

An enabled `decisions` section uses `schema_version: 2`,
`protocol: laya_systemone`, `bundle` with the three matching digests, `destination`
with this manifest, and `points.DP16` with `mode`, `threshold`,
`timeout_seconds` and optional class thresholds. Defaults remain 0.95 and 0.15 s.
The parser rejects `enforce`, other enabled points, LAN/TLS fields, inline secret
values, test injection and private-qualification booleans. Existing schema version
1/LAN configuration is preserved. Do not invent digests or widen deadlines to
make a test appear qualified; keep mode off until actual evidence is reviewed.

## Operator-controlled setup boundary

1. Obtain actual runtime/model/tokenizer/export/calibration identities and evidence
   on the existing Jetson; a configured hash is not measured loaded identity
2. Enter the key yourself through the existing approved profile secret workflow,
   under `LAYA_API_KEY`. Do not paste it into chat, a command line, this repository,
   a test fixture, URL, model state or a report
3. Review the active identity's explicit secret grant and exact
   `decision_inference`/`httpx` recipient grant. Preserve existing unrelated grants
   and profile boundaries; adding a destination is an operator authority decision
4. If health is needed, separately grant its exact URL. Inference grants do not
   authorize other paths, redirects, methods, dashboards or activity access
5. Qualify synthetic requests on the real Ryoko host through this owner-bound
   transport. Reproduce valid/missing/revoked-auth cases, route isolation, loaded
   service identity, cold start, boot without desktop login, and recovery

Construction and disabled configuration read no credential and open no socket.
Each call rechecks the current profile/identity/run, recipient and credential
reference before serialization, after DNS, and again after TLS before headers or
body. No ambient proxy, redirect, alternate host or retry is used. DNS resolves
once to an allowed public address; the connected peer and hostname-verified TLS
must agree. Request/response bodies and the whole operation deadline are bounded.

## Private data remains unavailable

Normal user conversation is private by default. Redaction, valid authentication,
shadow mode or a configuration flag cannot make it public or qualify transfer.
The client, codec and HTTPS transport independently deny private packets. No
Memory Harness recall or attachment fetching is performed to prepare a decision.

Before any private route is added, the operator must authorize exact destination,
identity/profile, purpose and data categories, and qualify storage, retention,
delete/restore, credentials, Cloudflare edge/origin visibility, logs, local activity
snippets, traces and crash dumps. The reported absence of public activity/dashboard
routes is insufficient to certify local retention. No server or tunnel changes
were made by this integration.

## Failure and recovery

All failures retain incumbent tools and deterministic authority. Missing bridges,
unclear/contradictory answers, stale catalog, unsupported response schemas,
capacity, deadlines, TLS/auth and storage failures do not grant an action.

One process-wide in-flight admission slot belongs to the physical destination,
not the current run or key. A timeout does not prove Jetson inference stopped.
Unknown completion quarantines that destination for the rest of the process;
creating a new client or rotating a key does not reset it. Investigate the actual
service and establish completion/outage recovery before an operator-controlled
restart. Do not automate restarts as a retry mechanism. No online reset boolean
is exposed. Durable budget uncertainty stays explicit until reconciled.

Receipt workers have separate bounded capacity. Receipts contain hashes, closed
outcomes, timings and counts, never raw state or authorization headers. Missing
required receipts disqualify reduction. Catalogs use exact alias maps; no fuzzy
lookup or guessed capability is accepted.

## Local validation and rollback

Run the canonical local `scripts/run_tests.sh` with the existing supported Python
interpreter and isolated test home. The transport suite uses a locally generated
TLS certificate for the exact hostname and fake `synthetic-laya-*` keys. That
Python-only fixture injection cannot be enabled in configuration or carry private
packets. Synthetic TLS evidence is not real-host qualification.

Turn DP16 off to stop admitting new decisions. Keep an existing conversation's
frozen prefix unchanged; recover authorized tools through the bridge until a real
new-context/committed-compression boundary. Revoked tools still fail dispatch.
No other decision points, model routes, approvals or budgets are enabled by this
runbook. Activation requires the exact qualified release and scope approval.
