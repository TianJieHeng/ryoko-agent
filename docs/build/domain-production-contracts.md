# BE10 bounded domain production

These adapters perform real local transformations and publish complete approved
artifacts through the existing BE07 catalog, BE06 effect ledger and BE09 mission
evidence. They do not add a model tool, provider loop, scheduler or competing
artifact store. Frontend presentation remains in its separate plan.

## Owned control surface

- `runtime.artifact.bytes.prepare/publish`: complete base64 input, at most 65,536
  encoded characters per request, exact MIME, project and optional revision.
  The same durable exact-byte approval, original lease, budget root, live grants
  and immutable history used for Markdown apply. Larger inputs require an
  already-authorized library consumer; this is not an unbounded upload API.
- `runtime.domain.prepare`: a 64 KiB `job_json` with `job_id`, `project_id`,
  `adapter`, and `arguments`. It performs bounded deterministic work and returns
  individual output approval IDs/digests plus an immutable JSON job manifest
  proposal. Request identity binds the canonical job; arbitrary imports, paths,
  clocks, credentials and executable callbacks are not accepted.
- `runtime.domain.publish`: the same job/command plus all exact ordered
  approvals. Inputs and grants are reopened, outputs regenerated and exact
  proposals checked. Each member publishes independently; the manifest publishes
  last. A failure can leave earlier members committed. The bundle is explicitly
  non-atomic; a notification retry never reruns production.
- Existing artifact status/cancel/get/recovery apply. Completion status supports
  domain bundles and brief receipts. Individual effects and artifacts remain
  inspectable after a partial failure or expired control claim.
- `python -m hermes_cli.domain_jobs request.json` validates request syntax for
  these controls; it does not execute, approve or publish a job.

Domain jobs permit at most 16 output files plus the manifest and 4 MiB total
output bytes. Exact source refs contain `artifact_id`, positive `version`, and
`sha256`. Same-project grants are checked on every read and publish. Metadata
records transformations, inputs, per-file validators and any domain uncertainty.
An original admission clock makes replayed tutor actions deterministic without
allowing the client to reset time or budgets. This is not wall-clock work polling.

## Formats and honest validation

The finite registry supports Markdown, UTF-8 text, CSV, JSON, notebook, restricted
XLSX, PCM WAV, restricted PNG, SRT and VTT. It validates complete bytes before
approval/publication and on reopening. The low-level broker repeats validation,
so a caller cannot bypass it by invoking the publication effect directly.
Old Markdown contract digests/receipts remain compatible with outstanding
approvals. Revisions cannot silently change MIME; conversion creates a new
derived artifact.

Structural openability is not visual fidelity. Receipts say
`visual_render=not_performed`; binary artifacts are `download_only`. Text is
source text, never active HTML. DOCX/PPTX/PDF conversion, broad Office features,
arbitrary audio/video formats and high-fidelity rendering are unsupported here.
Existing rich-format skills are not silently certified by filename extension.

- XLSX is a bounded ZIP/XML subset with explicit supported parts and rejection
  of macros, external relationships, unsafe XML, oversized archives and unknown
  features. Formula text and cached values stay distinct; caches are not claimed
  recalculated or used as trusted numeric inputs.
- Notebook output preserves source bytes, recipes and lineage. Code-cell text
  is an artifact, not permission to run it. Structural validation never executes
  cells or trusts a claimed execution count as external evidence.
- WAV checks real RIFF/chunks, PCM encoding, channels, sample rate, sample width,
  frame count and duration. PNG checks dimensions, supported pixels, CRCs and
  bounded complete decompression. SRT/VTT check finite ordered timing. No listening,
  visual or audio/subtitle synchronization acceptance is inferred.

## Research and living briefs

`SourceManifest` binds authenticated scope, exact immutable original version and
digest, authority declaration, acquisition/retrieval information, byte ranges,
freshness, availability, coverage and errors. Artifact and capture-original are
two local resolver types. A capture resolves its original bytes, not an annotation
or extraction. URLs are metadata and are never fetched by this adapter.
Authority classes are not flattened during deduplication. A quote found at an
exact byte range establishes membership, not semantic support for every claim.

Owned `runtime.research.resolve` returns bounded manifests. Manual
`runtime.brief.prepare/publish` replaces only affected tracked claim spans through
the existing targeted Markdown edit path, preserving untouched instructor/user
text and locks. Facts and interpretations are separate. Exact version/digest and
request-digest pins retain unrefreshed recommendations on their original evidence.
A separately approved JSON sidecar persists dependency sources and claims for
later resume; brief then sidecar publication is non-atomic. Resumed manifests are
data and must pass fresh byte/grant validation. Volatile completed-read times are
not fabricated to make approval replay deterministic.

Two live external connectors and P08 connected-source acceptance remain pending
their real tool contracts, operator configuration and grants. Local fixtures do
not establish that gate; primary memory is never substituted by local session
search. Scheduled refresh belongs to BE12.

## Data and decisions

The data adapter requires explicit encoding, delimiter, date convention, currency,
null tokens, units and duplicate policy. Ambiguous headers need manual mapping.
Joins declare cardinality and null-key behavior; aggregations declare null handling.
Known-answer totals and each output row retain exact source rows/digests. CSV,
restricted XLSX and complete notebook exports preserve the original dataset and
transformation recipe. CSV formula-leading text is refused rather than mistaken
for inert quoted text; numeric negatives and preserved workbook formulas remain
distinct. Supported bar/line charts use actual validated image bytes, not a promise
of an image from a manifest alone.

Decision records separate measured-value declarations, scenario assumptions,
subjective scores, priorities, weights/ranges and accepted choice. Exact rational
arithmetic and deterministic sensitivity recompute on changed assumptions. Source
artifacts can be independently reopened, but semantic truth of declared facts is
not certified by arithmetic. Acceptance does not authorize any external action.

## Tutor and educator

The installed versioned rational-arithmetic fixture has independent answer checks,
staged hints, bounded difficulty, visible history, explicit reset and user-chosen
revisit intervals. Held-out transfer exercises do not enter practice adaptation;
reset retains transfer-exposure history. Resume reads an exact scoped immutable
artifact and replays validated events, not free-form client memory. Cross-profile
substitution and changed history/content refuse.

Educator packages separately model prerequisites, objectives, timed activities,
sections, exercises and answer keys. Dependency-specific revisions preserve
unaffected content and instructor locks; arithmetic/alignment are checked while
instructor-authored prose is not falsely described as fact-checked. Complete
Markdown is supported. Teaching effectiveness and measured learning gains remain
an empirical study, not a repetition score or structural fixture result.

## Meetings and creative work

Supplied JSON/VTT/SRT transcripts preserve exact original bytes, timestamps,
overlap/gaps and uncertain/unknown speaker information. Speaker corrections retain
the original label. Processing requires a source-bound consent declaration and
bounded retention deadline; these are recorded declarations, not authentication
of the declarant or an automatic deletion/backup-erasure guarantee.
Proposed outcomes retain segment citations and stay proposals. No inferred promise
creates a message, ticket, schedule or accepted obligation.

Creative packages produce a complete brief/prompts/reference/continuity manifest.
Supplied assets are validated as actual bytes with explicit rights declarations.
Unknown rights require review. Prompt-only completion says external production is
pending; the adapter refuses to certify supplied labels as generated assets.
Live recording/transcription and media generation require their own actual adapters,
consent and operation-specific receipts.

## Coding and browser boundaries

Coding review packages reopen exact before/after Python artifacts, declared
repository/worktree/baseline identifiers, actual changed-file diffs, and BE09
authenticated isolated test receipts. They separate targeted-test evidence from
review/release/publication/merge/deployment. The stdlib-only isolated adapter cannot
establish a general host Git worktree or package ecosystem. Existing-file and
multi-file promotion, host Git and release execution remain uncertified.

Browser packages are read-only evidence diagnostics, with exact page/input/target
and age checks. Generic effect payloads and timeouts cannot certify browser success.
Unconfigured browser dispatch remains denied; unknown-after-submit remains unknown
and is never automatically retried. Auth/payment/high-impact handoffs are not
weakened by a source manifest.

## Qualification and rollback

Local acceptance exercises real bytes, exact approvals, publication, complete
reopening, scoped sources and actual isolated test execution. No live connector,
media provider, model download, credential or network-security change is made.
Human cleanup savings remain unmeasured; the BE09 paired-observation evaluator can
record a later actual comparison without treating synthetic fixtures as savings.

Adapters can be disabled independently by removing their explicit service entry;
no old artifact or original input is deleted. Keep compatible readers for all
published format receipts. Preserve partial publications, approvals and unknown
effects during rollback; never replay an external mutation to reconstruct a bundle.
