# BE13 configured local speech bridge

The existing runtime media RPCs now construct their session-owned VoiceIngress from the served profile's existing `stt` / `tts` settings. No core model tool, database migration, setting, credential, package or model installation is added. The legacy downloader/playback entrypoints are not called.

## Supported route and truthful readiness

- STT: explicit `stt.provider: local`, installed `faster_whisper` and `numpy`, and `stt.local.model` pointing to an existing complete local model directory containing nonempty `model.bin`, `config.json`, `tokenizer.json`, and `preprocessor_config.json`
- TTS: explicit `tts.provider: piper`, installed `piper`, and the existing `tts.piper.voice` `.onnx` file plus adjacent `.onnx.json`; named voices may resolve in the configured `voices_dir` or the existing served-profile Piper cache
- CPU/file process limits must be available. Other hosts report `speech_resource_limits_unsupported`
- Bare Whisper Hub model aliases, remote STT/TTS, custom commands and plugins are not resolved by this bridge. Missing assets/packages remain unavailable. A complete local model path can be chosen through the existing configuration workflow; this bridge never modifies it
- `voice.health: prerequisites_verified_not_loaded` is a filesystem/package preflight, not a model load or successful inference claim. The capability query never loads a model. Invalid or incompatible local assets can still fail on an explicit operation with `speech_model_load_failed` or `speech_operation_failed`
- Budget-enforced sessions use the existing durable BE03 tree, with an explicit finite speech-admission control before first-turn capture when necessary. Missing, malformed or changed policy still fails closed; cancellation remains available
- `processing_location: local` means the gateway backend's local model. `voice.processing_host: gateway_backend` makes that explicit. It does not assert that the client and backend are the same computer

This change does not qualify a live speech model or physical microphone/speaker. All automated inference evidence uses deterministic local package-boundary fixtures.

## Frozen wire contract

Existing non-budgeted inputs and the `response_json` envelope remain compatible. Every request includes `session_id` and `schema_version: 1`. Strict budget clients additionally use the admission/control fields below.

- `runtime.media.capabilities`: `voice.stt`/`tts` retain their declared local, nonstreaming adapter records. `unsupported_reasons` is a map of unavailable kind to symbolic reason. Added limits describe PCM, text, output and duration bounds. `playback` is `client`; `backend_playback` is false
- `runtime.voice.admit`: explicit `request_id` creates a real finite human control through the existing command journal, writer lease and run-budget admission, then immediately finalizes that control. It returns `budget_account_id`, `root_id`, the original effective `deadline` and a budget-state snapshot. It never starts capture, inference or a mission. Existing current accounts are reused, including on retry/reconnect; expired/exhausted accounts fail without renewal
- `runtime.voice.capture.start`: optional `request_id` and `budget_account_id` are required in strict mode. Returns `capture_id`, `state: recording`, and `processing_location: local`
- `runtime.voice.capture.feed`: `capture_id`, `sequence` (starting at zero, strictly contiguous), `pcm_base64`, `final`. Input is mono 16 kHz signed little-endian 16-bit PCM, at most 64,000 decoded bytes per chunk and 1,920,000 total bytes within 60 seconds. An empty final chunk is supported after earlier PCM; a completely empty recording is rejected. Non-final transcript text is empty for the configured nonstreaming route
- A final feed returns `state: transcribed`, the next sequence, `text`, `final: true`, `accepted_as_task: false`, `confirmation_required: true`, and processing location. Transcripts are bounded to 65,536 UTF-8 bytes
- `runtime.voice.capture.cancel`: discards the caller's capture and interrupts its in-flight transcription; it never cancels a mission
- `runtime.voice.speak`: optional `request_id` and `budget_account_id` are required in strict mode; explicit text, at most 4,096 UTF-8 bytes. Returns `state: ready`, an opaque `speech_id`, `playback: client`, processing location, `mission_cancelled: false`, and `audio: {format: pcm_s16le, sample_rate, channels: 1, pcm_base64, byte_length, sha256}`. PCM is exact, at most 1,048,576 bytes, 8–48 kHz and at most 30 seconds. `ready` means synthesis returned verified audio, not that a client has played it
- `runtime.voice.stop`: cancels only the caller's synthesis generation and returns `state: stopped`, `mission_cancelled: false`, `streaming_stopped: false`. The client must stop its own AudioBuffer source and invalidate pending speak results on stop, barge-in, disconnect and unmount. There is no backend speaker access
- `runtime.voice.submit`: exact reviewed `text == confirmed_text`, normal `binding_id` / `input_id` / optional `expected_revision`, then the existing durable command queue. Capture, transcript, synthesis and speech-stop never admit or cancel accepted work

All reads/actions are bound to the existing runtime principal/profile/agent/session and current transport. A completed operation rechecks the transport and context before returning private output. A reconnect cannot receive the retired transport's result through that old request.

## Execution boundaries

The factory accepts no module, executable, destination or provider override from RPC inputs. Each explicit operation runs the fixed first-party `agent/speech_local_worker.py` using the current interpreter with `-I`. Only allowlisted installed package imports and resolved local model data enter the worker. Faster-whisper construction uses `local_files_only=True` with a complete tokenizer; Piper loads exact existing files. There is no remote fallback, lazy installation or download-on-miss path.

The child environment is built with the served-profile helper without credentials, then narrowed to essential locale/runtime metadata. It omits Python paths, proxies and unrelated secrets. Offline package flags and a Python network/subprocess audit guard reject Python-level remote resolution and subprocess launch. This is a trusted-package adapter, not a sandbox for adversarial third-party code; it does not claim OS-level containment of arbitrary native extensions.

Each request has a 30-second inference wall ceiling, at most 20 CPU-seconds of hard process allowance, capped output file size, bounded PCM/WAV buffers and no core dumps. Strict worker limits are shortened by the configured request timeout and original account/mission deadlines, including bounded startup and termination allowance. There are two worker slots per gateway process. Capture and speech ownership locks are released while model work runs, so cancel/stop can invalidate an in-flight result. Cancelled/timed-out processes receive kill and a bounded reap. If termination cannot be acknowledged, the response is `speech_termination_unknown`; both the durable tree reservation and the host worker slot remain held. A stale writer also cannot refund an outstanding reservation. Private temporary input/output files are removed. No transcript/audio is written to mission history or durable storage unless the user explicitly submits reviewed text.

## Strict aggregate accounting

`voice.budget` advertises `mode: existing_run_tree`, `admission_rpc: runtime.voice.admit`, required control fields and `first_turn_without_account: explicit_admission_required`. First-turn admission never manufactures an active `RuntimeRun`. Its actual finite control account remains the session's first tree ceiling; later real task runs inherit that tree. An active/newer task's exact account supersedes the voice account. Client-selected sibling, foreign-session, retired-policy and old-root borrowing are rejected. An existing mission keeps its identity and content unchanged.

Capture start, empty/oversized input and cancelled recordings allocate no worker reservation. A validated final STT or TTS physical process reserves one executor slot, one attempt and a finite wall allowance in the existing account and all ancestors before process creation. Successful, failed, cancelled and timed-out work settles measured wall time and physical attempts after known termination. Process-creation rejection consumes zero attempts. Uncertain creation/termination retains worst-case units and concurrency; actual overruns create sticky debt rather than being clamped. No provider/token/cost work is fabricated for these offline workers.

Strict request IDs are stable per operation kind and session. Once a reservation exists, retry/reconnect returns `speech_request_consumed` (or an ownership/idempotency conflict) without rerunning the worker or replaying audio. Final successful responses include the durable `budget` reservation receipt. Admission returns aggregate account state, but there is no separate speech-receipt inspection RPC. No audio/transcript bytes are stored in the budget ledger. Playback remains client-owned and must be stopped independently.

## Validation

The fixture tests execute the real configured factory, real RPC authority and real subprocess worker while replacing only the installed speech package boundary with small on-disk deterministic packages. They cover A→B→A profile routing, environment scrubbing, byte length/digest equality, missing assets/packages, unsupported providers, no model invocation during capabilities, output/CPU/wall bounds, live cancellation and child reaping, offline network denial, model-load failures, stale-owner suppression, malformed-policy denial and confirmation-only task admission. The strict extension additionally covers first-voice control with/without an existing mission, same-root continuation, no-renewal retries and expiry/exhaustion, concurrent executor quota, CPU/wall cancellation, lost termination acknowledgment, uncapped debt and durable reopen/no-replay. See `be13-speech-validation.json` for the original bridge and `be13-strict-speech-validation.json` for the strict extension receipts.

Remaining qualification: real installed model quality/latency; actual client microphone permission and playback; native Windows process-bound support; streaming STT; live cross-device UX and measured user benefit. No claim that these gates have passed.
