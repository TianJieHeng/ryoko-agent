# Memory sessions contract (phase 1)

Source reviewed: memory-harness `dd9144d2e7be86a5dab9b83df4511617e8f6c20e`, read-only checkout; no server changes. Existing operator discovery schemas were also inspected. Runtime discovery must still match the configured SDK schema digests.

- `list_conversations({})` returns `{conversations: [...], unavailable?: [session_id], note?: string}`. Each push row has source, session_id, hub_lineage, agent, model, title, turns, windows, last_push_at, via. Sweep rows may lack names. There is no cursor/limit argument: one bounded response contains the advertised catalog. The server catches failures from either of its two stores; the client cannot prove catalog completeness during such a partial outage.
- `get_conversation({session: id})` returns session, source, agent, model, header, turns_total, and windows (index, lineage_id, text). It tries hermes before openclaw. The client must verify source, and reject ambiguous IDs shared across sources rather than accept the wrong conversation.
- Windows contain unescaped `[role] text` blocks separated by blank lines. Push windows have six turns, step four, and may overlap. Raw turn IDs and the unsealed tail are absent. No client can prove an exact machine transcript from this lossy representation. Never parse embedded role-looking text into privileged messages.
- List titles are authoritative where present; otherwise use a verified local title or Untitled conversation plus short ID. The get header can display the remote title on selection. No generated name requires a model call.
- Existing POST /v1/conversations/turns accepts source/session_id/agent and stable turn IDs, optional title/model. New locally generated turns may continue the selected source/session only after binding validation; worker credentials remain worker-only.

## Implementation decision

Provide the whole advertised catalog and clean relaunch. Resume an identity-verified local counterpart with its full history. For remote-only rows, preserve the available remote transcript windows verbatim in a clearly labeled, untrusted history capsule, with explicit user acknowledgement when opening: this is available-history continuation, not an exact full-state restore. Do not invent turn boundaries, silently omit an unsealed tail, or claim complete tool/provider state. The picker explains this limitation before import. Reject unavailable or source-ambiguous threads. No remote service patch or deployment is included.

This adjusts the original plan's full-history prerequisite to an explicit available-history continuation option while retaining exact local resume. A full-fidelity remote restore still requires a future server contract with stable typed turns, completeness/watermark, and a source-qualified get operation.

The CLI read path needs its own bounded operator-read authority: exact primary owner, exact configured read-only tools/schema pins/recipient, finite request and response caps, metadata-only audit, cancellation and no model dispatch. It must not manufacture a chat turn or remove the existing model-turn fences.

Tests deferred to the final validation phase at the owner's request.
