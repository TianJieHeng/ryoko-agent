# BE13 bounded service and media ingress

## Supported consumer paths

The owned JSON-RPC methods in `tui_gateway/methods_media.py` validate the existing
live transport, stored agent identity, active profile, and current policy. They
do not accept caller-provided principals, permission grants, executor claims,
conversation history, credentials, file paths, or remote endpoints.

All methods return a bounded `response_json` projection. Input DTOs and method
names are declared in `tui_gateway/contracts/media.py` and included in the
generated gateway contract. These are backend consumers; this phase does not
claim an implemented voice or screenshot frontend.

## Authenticated two-stage local document pipeline

1. `runtime.services.capabilities` exposes two real finite adapters:
   `local.text.normalize` version 1 converts granted Markdown UTF-8 line endings;
   `local.document.structure` version 1 extracts ATX heading records and literal
   text into JSON. Neither executes document instructions, starts a shell,
   invokes a model, or uses a network. Heading extraction is a bounded syntax
   utility, not a general Markdown parser or semantic summarizer.
2. `runtime.services.prepare` accepts one immutable project artifact version.
   The preview identifies source digest, source size, input/output schema,
   adapter/version, processing and storage locations, limits, and a live locally
   authenticated executor reference. There is no material transfer at prepare.
3. `runtime.services.execute` requires the exact preview digest. Before each
   uncommitted stage, it checks the live executor/process reference, live policy,
   project grant, data locality, and bounds. The first stage reads and verifies
   the exact authorized artifact through the existing artifact reader.
4. Each stage commits output bytes and its transfer receipt in one SessionDB
   writer transaction. A receipt contains input/output digests, byte counts,
   MIME type, adapter version, destination, executor reference, and transfer ID.
   The acknowledgment is a local SQLite commit, not a remote or user receipt.
5. Loss of a service or executor returns pending/partial state at the first
   affected stage. Completed stage bytes are digest checked and never rerun.
   A crash before a commit can repeat only that pure finite computation.
6. Re-preparation of the same exact input can disclose a fresh authenticated
   local executor after disconnect/expiry. This produces a new preview digest;
   the old digest cannot authorize execution. It preserves completed receipts.
   No process execution is described as resumed and no private work migrates
   to another host.
7. `runtime.services.status` reads receipts; `runtime.services.output` returns
   final private staged bytes with the output digest. Publication is separate:
   the user must use the existing exact artifact prepare/publish approval path.
   This service never silently changes a canonical artifact head or sends data.

Each stage has a 64 KiB input/output limit, a two-second finite-computation
deadline, and at most 256 extracted headings. The deadline rejects a late result;
it is not a promise of preemptive thread cancellation. Only the two built-in
finite algorithms are callable. There are at most 256 pipeline records, two
stages per record, keeping stage storage bounded to 32 MiB plus metadata.
Capacity exhaustion is explicit; automatic private-data deletion is not enabled.

Persistence tables are `bounded_service_pipelines`, `bounded_service_stages`,
and `runtime_channel_bindings`. They belong to the same profile SessionDB and
must follow that store's backup/export/erase policy. Stage outputs contain private
document data and are not general shared content-addressed storage.

## Speech boundaries

The default runtime reports STT and TTS as unconfigured and rejects capture or
speech requests. This does not claim a microphone, a live transcription model,
or a configured speech engine. Trusted hosts can supply explicitly declared
local adapters through `VoiceIngress`; no RPC can register code or declare
itself authenticated. Remote processing is currently rejected.

`runtime.voice.capture.start/feed/cancel` implement an explicit client-PCM
push-to-talk state machine, with transport ownership, sequence checks, 16 kHz
mono signed 16-bit PCM, a 64,000-byte chunk cap, a 60-second/1,920,000-byte
capture ceiling, optional partial feedback, and final transcript delivery.
Capture data is ephemeral and discarded on finalization/cancel. Transcripts
always report `accepted_as_task: false`.

`runtime.voice.submit` requires exact confirmation of the whole text, including
names and numbers, and uses the ordinary durable command admission queue.
`runtime.voice.stop` and push-to-talk barge-in interrupt only owned TTS.
Stopping local UI streaming remains the client's responsibility; cancelling a
mission requires the normal explicit runtime cancel command.

The adapter lifecycle is tested with local deterministic test adapters. No live
STT/TTS provider, latency guarantee, hardware device, or remote speech transport
was configured or tested in this implementation.

## Selected-window inspect, guide, and act

`runtime.screen.capture` accepts only an explicitly supplied selected-window
PNG, bounded to 1 MiB and 4,194,304 pixels. It validates the PNG and decoded image,
then retains metadata, a digest, and a transport-bound frame reference. It does
not acquire a screenshot itself, store screen pixels, run OCR, or infer text.

`inspect` reports dimensions/digest/window scope. `annotate` validates an overlay
rectangle and gives guidance for the supported workflow. `screen.submit` acts
only by submitting explicitly confirmed selected text through normal command
admission. General OS clicks, keystrokes, purchases, and other consequential
screen actions are unsupported. Such effects retain the runtime's existing
approval requirements.

Every region is bound to the current frame reference and transport, with a
15-second server-receipt-age limit. A newer frame invalidates the old one.
The client acquisition time is unverified, so this is not advertised as proof
that the pixels still match the physical screen. That limitation is one reason
this initial workflow cannot perform general OS actions.

## Same-work channel handoff

`runtime.channel.bind` derives principal/profile/agent/session/project/mission
mapping from the live authenticated transport and authoritative mission. It
supports `local_jsonrpc`, `voice`, and `screen` labels on that owned transport.
A binding is not a login token and cannot grant access to an unattached caller.
External Slack/other channel adapters remain unconfigured.

`runtime.channel.submit`, `runtime.voice.submit`, and `runtime.screen.submit`
derive the same command ID from actor, mission, and logical input ID, excluding
channel. A duplicated input across surfaces is accepted once; changed text under
the same input ID conflicts. History is never accepted or replayed as commands.
These methods feed `_submit_runtime_prompt` and the existing `AdmissionQueue`,
or the existing `submit_command` for explicit steer/cancel controls. No second
agent loop, provider path, outbox, or mission authority is introduced.

## Validation

`tests/agent/test_media_ingress.py` covers declared/unconfigured speech, bounded
sequenced transcription, barge ownership, scope/region checks, exact confirmation,
and stale/replaced frames. `tests/tui_gateway/test_media_rpc.py` covers real owned
dispatch, two-profile separation, project revocation, executor disconnect and
renewed preview, two-stage transfer/reopen/recovery without replay, private output
digests, unsupported capabilities, and cross-channel deduplication in the actual
durable admission queue. Fixtures contain no live user data or provider calls.
