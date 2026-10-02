# Recipient plans and transport coverage

BE05 configured agent identities require an immutable `recipient_plan` before
outbound work. A missing plan and an empty grant list both deny network traffic.
Profiles without agent identity configuration retain legacy behavior and are not
certified by this boundary. Policy/configuration digests include the plan; removing
or replacing an established plan cannot silently resume an old session.

## Policy shape

The field belongs to each configured `AgentPolicy`, including the child ceiling:

```json
{
  "recipient_plan": {
    "schema_version": 1,
    "envelope": "declared",
    "grants": [
      {
        "recipient_id": "operator-chosen-recipient",
        "purpose": "main_model",
        "endpoint": "https://operator-configured.example/v1",
        "transport": "httpx"
      }
    ]
  }
}
```

This is an illustrative configuration, not a working endpoint or permission to
configure a service. Endpoints are explicit, credential-free HTTP(S) base URLs.
No wildcard, automatic recipient selection, credentials embedded in URLs,
query/fragment, traversal or ambiguous encoded path is accepted. The transport
checks the same origin and base-path boundary at every physical request. Host
headers cannot change the HTTP virtual-host recipient. Model adapters additionally
admit only `POST` to the exact base endpoint's `/chat/completions` route.

Purpose values are `main_model`, `aux_model`, `memory`, `embeddings`, `mcp`,
`browser_tool`, `message_delivery`, `telemetry`, `provisioning`, `training`, and
`subprocess`. Declaring a purpose never certifies its execution adapter.
`httpx`, `subprocess`, `browser`, and `opaque` are recognized transport labels;
only the installed HTTP boundary can presently consume HTTP grants. Other
transport labels fail closed. Child plans intersect the parent's and the ceiling's
exact grants, without widening either side.

## Local-only envelope

`envelope: local_only` requires every recipient to use an explicitly declared
literal loopback IP. It rejects DNS aliases, including `localhost`, and all LAN
addresses. A Jetson on a LAN is a distinct recipient and must be explicitly named
in a `declared` plan. A local main model alone makes no statement about auxiliary
models, embeddings, memory, tools, delivery or telemetry.

This envelope describes supported runtime routes, not all programs or traffic on
the operating system. Certified generated-code execution has a separate actual
OS-level no-network boundary; it cannot acquire arbitrary network access merely
because the plan contains a subprocess grant. Unsupported executors stay denied.

## Implemented adapters and unsupported routes

- Main and auxiliary model routes use guarded synchronous/asynchronous HTTPX
  transports and the concrete OpenAI chat-completion SDK. Every default and
  mounted transport must be guarded. Credentials and payloads reach the transport
  only after live policy and durable owner-generation checks
- Cross-recipient redirects, endpoint changes, revoked/disabled policies, absent
  trusted identity, custom unguarded transports, ambient proxies and disabled TLS
  verification fail closed. SDK retries remain disabled; existing BE04 fallback
  refusal, client cache scopes, account circuits and per-client socket ownership
  are preserved
- Strict auxiliary resolution requires an explicit endpoint/model and supported
  route. It does not invoke discovery fallback, opaque provider constructors,
  command-token sources or OAuth refresh. Opaque custom auxiliary callbacks are
  denied before running, including optional title-generation callbacks which
  otherwise could cancel a bounded run while probing an unsupported adapter
- MCP connects through its independent agent/server/transport/secret/schema grant
  plus this recipient boundary. Discovery can run without a model turn; actual
  calls remain subject to the MCP capability and run fences. MCP redirect targets
  cannot change the admitted recipient, and environment proxies are disabled
- External memory and background review remain disabled by the earlier strict
  identity lifecycle pending per-agent memory routing. Model SDK embeddings and
  non-chat operations are explicitly unsupported. Browser, external delivery,
  telemetry/plugin callbacks, provisioning, training and unrestricted subprocess
  routes do not become available from a purpose grant
- Strict external-channel runtime execution is denied before model dispatch until
  a recipient-bound delivery adapter is available. This is not a claim of global
  interception of unrelated gateway or daemon background traffic

Recipient permission and spending permission remain independent. The BE03 budget
writer is the sole resource accounting authority; egress grants never certify an
unsupported budget adapter or refund uncertain remote usage.

## Validation boundary

`tests/tools/test_egress_policy.py` exercises real loopback HTTP listeners and real
OpenAI/HTTPX sync and async clients with harmless fixture values. It verifies
successful explicitly permitted requests, a redirect whose second server receives
no headers/body, rejected SDK operations, endpoint/Host changes, and a policy
revoked before the next network request. SQLite-backed ownership checks verify
owner-generation fencing. Separate deterministic cases cover all purposes, local
versus LAN envelopes, immutable/intersected plans, strict missing plans, replay
policy removal, opaque callbacks and shared-pool cancellation ownership.

These receipts do not certify a live provider, actual billing, external DNS/TLS
infrastructure, all operating systems, background gateway traffic, or arbitrary
plugin code. The phase validation receipt and journal carry the exact test
commands, final source revision, pass counts and remaining limitations.
