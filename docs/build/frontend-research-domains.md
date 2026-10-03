# Bounded research and selected domain controls

These shared helpers are consumers of the generated schema-v1 gateway contract.
The backend remains responsible for live identity, grants, approval scope,
immutable bytes, transformation execution and publication. No schema changes,
new RPCs, credentials, scheduler, model execution or remote production are added.

## FE05: research and manual living-brief refresh

`runResearchCommand(argument, request, sessionId)` accepts:

- `--help` / `help`: concrete command forms and limits
- `list`: the supported retained local source types, not an invented source catalog
- `resolve <JSON source array>`: exact granted project-artifact/capture-original
  reads with source version, digest, evidence byte ranges, coverage, missing and
  stale status. Quotes and private identity scopes are not printed
- `inspect <command-id>`: existing owned artifact-command status, plus a committed
  brief result when available
- `prepare <JSON>` / `publish <JSON>`: manual refresh of an existing brief using
  an immutable prior dependency manifest
- `prepare-initial <JSON>` / `publish-initial <JSON>`: establish the dependency
  baseline for an existing brief artifact through owned source reads

Durable refresh JSON has `project_id`, `command_id`, `request_id`, `artifact_id`,
`parent_version`, `manifest_ref` (`artifact_id`, `version`, `sha256`) and
`request_json`. The last field is an exact JSON string containing `requests`
(source requests) and `updates` (`claim_id`, `replacement`). Publish repeats the
identical parameters with `brief_approval_id`, `brief_approval_digest`,
`manifest_approval_id`, and `manifest_approval_digest` from preparation.

Initial preparation uses the same project/command/request/artifact/parent fields,
plus `previous_requests`, `claims`, `requests`, and `updates`, rather than
`manifest_ref`/`request_json`. Claims contain `claim_id`, `section`, `text`,
`kind` (`fact` or `interpretation`), `section_sha256` and exact `citations`
(`source_id`, `range_index`, optional version/digest pins).

The initial path obtains `previous_sources` from `runtime.research.resolve` on
the current session. Callers cannot supply identity-bearing manifests. The exact
resolved request, including read timestamps, stays in a bounded transient cache
keyed by the **stable RuntimeRequest function**, owned session and command ID.
The renderer must retain the same request function across calls. Initial publish
accepts only `command_id` and the four exact approval fields. It cannot switch
sessions or alter the prepared request. Repeating preparation after an uncertain
response reuses exact bytes; it does not silently resolve a new baseline.
Reconnect/reload recovery uses the original command inspector, rather than
inventing new approvals. Subsequent published-manifest refresh is stateless.

Prepared, published, partial publication and delivery are different states.
Brief and dependency manifest publication is not atomic. A missing second commit
retains the first committed brief and reports manifest uncertainty. Mutation
errors make no automatic retries and do not print arbitrary backend error text.

This is **local retained-source research**, not connected-service discovery.
Claims remain semantically unverified and source authority is caller-declared.
No automated monitoring, independent fact verification, or decision/scenario UI
is claimed. Creating the original Markdown brief uses the existing artifact
controls; this module prepares targeted refreshes of its exact version.

## FE07: selected deterministic data and creative packages

`runDomainCommand(argument, request, sessionId)` supports `list`, `--help`,
`inspect <command-id>`, `prepare <JSON>`, `publish <JSON>` and
`lineage <JSON>`.

Prepare JSON contains `command_id` and `job_json`, an exact string containing
`job_id`, `project_id`, `adapter`, `arguments`. Publish repeats the unchanged
command and job string plus ordered `approvals` entries with `approval_id` and
`approval_digest`. The manifest is the last proposal and last publication.

### Data

`adapter: "data"` takes `inputs` and `recipe`:

- Each input has `source_id`, exact `ref` (`artifact_id`, `version`, `sha256`),
  and explicit `options`: encoding, delimiter, date format, currency, null
  markers, units and duplicate-key policy. Optional column mapping is positional
  or name-based; types, separators, keys and worksheet selection are validated
- Recipe names `base` and optional finite joins, aggregation, charts and exports
- Supported choices: inner/left joins with declared cardinality and null policy;
  sum/mean/min/max/count; bar/line charts; CSV/XLSX/IPYNB exports
- Formula caches are preserved without claiming recalculation. Source byte
  reading, MIME/formula validation, numeric calculations and lineage are backend
  responsibilities, not recomputed by this frontend

`inspect` reads the published immutable JSON manifest through owned artifact
chunks. It checks each scope/version/digest/offset, then the whole-file SHA-256
before rendering profile missingness, units, formula status and recipe summary.
`lineage` accepts `project_id`, `manifest_ref` and a zero-based result `row`.
It returns source row references and digests, never a raw source-cell dump.
Large row reference lists explicitly show only the first 50 entries; the complete
lineage remains in the retained manifest.

### Creative

`adapter: "creative"` takes `brief`, `prompts`, optional `assets` and
`continuity`. Assets require a safe `name`, exact `ref`, declared `rights` and
`stage: "supplied"`. Generated stages are rejected before RPC.

Preparation and publication produce a **prompt-only package** with zero media
generation. Supplied reference bytes can be validated, but rights are only
declarations. Publication never implies external production, delivery or legal
rights verification. Private prompts and raw brief text are not printed by the
summary renderer.

Teaching, tutoring, demonstration, meeting, coding and decision controls are
explicitly deferred here even where another backend adapter exists. Unsupported
formats and adapters fail closed without remote fallbacks.

## Focused verification

Behavioral TypeScript fixtures cover missing/stale sources, exact approvals and
request bytes, initial owned-read derivation and session isolation, partial brief
publication, prompt-only zero-generation behavior, supported data shapes,
unsupported formats/stages/authority overrides, uncertain publication and
digest-verified source-row lineage. Real isolated backend RPC suites exercise the
unchanged source-grant and publication paths, including real CSV→workbook,
notebook/chart output and non-atomic brief recovery.

These checks are bounded shared-command/backend evidence. They do not establish
live connected-source P08 acceptance, visual desktop/TUI end-to-end acceptance,
all FE05 decision requirements, or all FE07 domain breadth.

## Typed desktop review

`ResearchPanel` provides retained-source fields and source-bound brief review. `DomainPanel` provides labeled data assumptions, aggregation/chart/export options, and creative prompt-only/reference inputs. Both preserve uncertain controls, disable stale approvals, and require terminal status/cancellation before replacing a preparation. Domain publication keeps the confirmation owned until its request settles, does not retry unknown writes, and normalizes the exact three-field manifest reference before digest-verified readback. Local oversized jobs fail before a backend control is reserved. New data/creative component checks include all nine locale chrome sets, disconnect/scope changes, partial outcomes and actual manifest lineage. See the per-phase validation receipts for precise counts and remaining gates.
