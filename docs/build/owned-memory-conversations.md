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

The current deployed MCP supports thread listing/reading/search and durable
memory writes. `ingest_conversations` is a server-local import and is not the
remote Hermes conversation workflow. Companion `memory-harness` commit
`1b81bf5` exposes `append_conversation` using the existing harness live-turn
API, and includes ordered source turns in transcript reads before search
windows fill. Deploy both harness and MCP before local activation.

After authenticated discovery, pin and grant the actual append schema as a
mutation. Its result contract requires nonempty `hub`, `session_id`, integer
`accepted` and `duplicate`; set `receipt_field: hub`. Repin the changed
`get_conversation` schema too. Only then add this opt-in configuration:

```yaml
memory:
  conversation_mcp:
    server: ryoko_memory
    append_tool: append_conversation
    source: hermes
    agent: ryoko
```

Start a new session. This activates synchronous capture before the durable
turn closes: the clean user text and final assistant response, redacted at
source, with stable command-derived IDs and acceptance timestamps. Tool
internals, reasoning, system prompts and injected memory are not exported.
Multimodal content contributes its explicit text blocks only. These are
conversation records, not extracted personal memories. Existing threads
remain available through MCP list/get/search tools.

The run result includes `conversation_sync.status` (`confirmed`,
`not_confirmed`, or `no_visible_text`). A failed save emits a warning without
logging private contents. Request-size, budget and uncertain-effect limits
still apply. Automatic capture is currently **not activated** in `ryoko`
because the deployed server does not advertise append yet. A Git push does
not deploy that server. After deployment, verify a real append/read-back and
receipt before claiming conversation capture is live.
