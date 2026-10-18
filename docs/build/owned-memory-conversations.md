# Owned skills, memory writes and conversation capture

The `ryoko` deployment uses its existing OpenAI Codex OAuth login and the
primary-only memory MCP bearer. LAYA remains bypassed.

Skills are profile-local document reads. Instructions and support documents
under references/scripts/templates/assets are readable without automatically
executing shell templates, loading plugins, or exposing sibling secret files.
Script execution uses its separately granted executor. The Linux namespace
executor requires the host AppArmor profile in `host/ryoko-unshare.apparmor`.

MCP writes require an explicit `mutation_contracts` entry, an input schema,
result schema and receipt field, plus the existing schema pin, recipient and
physical-request budget. A write gets a durable effect before the physical
request. Authentication/session recovery cannot replay it. Missing or invalid
receipts produce an unknown outcome and block further dispatch for the run;
an unresolved matching write is not replayed automatically in later runs.
Confirmed identical calls within the same run return the effect state.
The adapter does not invent provider idempotency or a monetary cost.

## Conversation capture and delivery

MCP provides thread listing/reading/search. Conversation writes use the existing
REST endpoint `https://memory.ryoko.okinawa/v1/conversations/turns`.
The owner's server-side `RYOKO_CONVO_PUSH_TOKEN` is a different credential from
`HERMES_MCP_TOKEN`; it is read only by the background worker, not granted to the
model, installed skill, child agents, or MCP tools. No harness modifications,
new MCP append tool, or database mount are required.

The profile opts into capture with:

```yaml
memory:
  conversation_push:
    enabled: true
    endpoint: https://memory.ryoko.okinawa/v1/conversations/turns
    token_file: /absolute/private/path/conversation_push.env
    source: hermes
    agent: ryoko
```

The token file contains `RYOKO_CONVO_PUSH_TOKEN=...` and must be owner-only.
The queue lives in the profile's private `conversation-outbox/outbox.sqlite3`.
User text is queued on durable turn admission; the final assistant reply is
queued on completion. Capture includes explicit text blocks from multimodal
input, excludes reasoning/system prompts/injected memory/tool internals, and
redacts secrets before queueing. It does not extract personal memories or make
an additional model request. Oversized turns fail visibly rather than being
silently truncated; the per-request bound is 65,536 UTF-8 bytes.

The worker polls every two seconds, independently of the CLI lifetime:

```sh
hermes --run-module agent.conversation_worker --profile-home /absolute/profile
hermes --run-module agent.conversation_worker --profile-home /absolute/profile --status
```

A named-profile user service template is provided at
`host/hermes-conversation-worker@.service`. Install it under
`~/.config/systemd/user/`, then use:

```sh
systemctl --user daemon-reload
systemctl --user enable --now hermes-conversation-worker@ryoko.service
```

The `ryoko` installation also enables user lingering so the worker can survive
logout. Stop delivery with `systemctl --user stop ...`; disable profile capture
with `memory.conversation_push.enabled: false`. Restart the service after code
updates. It reloads profile settings on each polling cycle.

Delivery preserves stable source/session/turn IDs and immutable payload bytes.
The harness's existing turn deduplication makes timeout/restart retries safe.
The worker holds an exclusive per-profile process lock, journals each attempt
before sending, and sends turns in order within each thread. A stalled thread
does not block other threads. A changed destination/owner cannot redirect the
old queue; those records remain retained with their original binding.

Only a matching session receipt, nonempty hub and accepted+duplicate count
mark a turn sent. Delivered payload text is removed from the queue; its hash,
ID and receipt remain. Transient failures back off to a maximum of 120 seconds;
401/403 retries wait 60 seconds so a replaced token can recover. Redirects and
permanent request errors block that turn without dropping it. Responses are
bounded to 262,144 bytes, HTTP requests have a 15-second timeout, and redirects
and ambient proxies are disabled. Service shutdown is bounded to 20 seconds.

Inspect `--status` or the user service journal; neither prints transcript or
credential contents. For a corrected permanent error, stop the service, run
`--retry-blocked --once`, then start it again. Never alter a queued payload
under its original turn ID. A run's `conversation_sync.status: queued` means
local durable acceptance, not confirmed remote delivery; the worker's receipt
is the delivery evidence.

Read the resulting threads through existing MCP list/get/search tools. The
server's existing search-window rules still apply: a short live tail may not
appear in search/transcript windows until the server seals it. Its REST session
state and delivery receipts report accepted turns without waiting for sealing.
