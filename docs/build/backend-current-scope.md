# Current backend implementation and qualification scope

This is the current disposition of the selected backend build, not a release approval.
The immutable full-run candidate was `b323e73f7b2f6792c57341240e206a72e43e3192`.
The post-campaign source checkpoint is `cb889631b43e8cb252d30537c3678b95ba0e754f`;
its targeted repairs and qualifications are separate from the original full run.
Earlier phase receipts describe their own historical source; their missing-feature
lists must not be mistaken for the current implementation, or silently erased.

## Final observed-failure disposition

[All 117 nonzero module receipts and 484 failing node outcomes are reconciled](be18-failure-resolution.json):
376 outcomes recovered, 108 host-blocked, zero unclassified failures, and no
unfixed production regression identified among the observed failures. Two
pre-existing production defects were repaired; this is not a claim of no latent
bugs. Setup/teardown node counts and the original runner's 219 failures are
different measures and must not be subtracted or rewritten.

Remaining: 101 socket-EPERM outcomes and seven profile-export fixtures requiring
an actual temporary root outside Git ancestry. Use the canonical runner on the
[remaining module list](remaining-host-tests.txt) in a suitable already-authorized
host; do not disable guards, hide protected markers, or change host security
permissions to force a pass. The LSP zombie-cleanup fixture was safely repaired,
not bypassed. Combined final repair integration passed 182 tests; its subsequent
LSP-only delta passed 13, and all 48 changed Python files passed Ruff.

The failed full campaign is retained. No redundant full rerun was used to repeat
known host restrictions. Native/browser/live/human-pilot/Dots gates remain open,
and later commits remain unpublished.

## Required local gaps closed after the first BE18 campaign

| Earlier gap | Current implementation evidence | Remaining boundary |
| --- | --- | --- |
| Record-only monitor intent | [Local notification receipt](be12-notification-validation.json): real cron-to-outbox delivery, quiet/DST/digest/snooze/dismissal, exact-byte and rendered-text acknowledgments | Acknowledgment semantics differ by client; third-party delivery and live human-read outcomes are unqualified |
| Stored template without application | [Project reuse receipt](project-reuse-validation.json): actual immutable Markdown template preview/application/publication and workflow reuse | Unsupported legacy style-only templates fail explicitly; generative quality is not certified |
| Output influence inspection/control | [Project reuse receipt](project-reuse-validation.json): exact supplied memory references and scoped future-response controls | This is supplied-input provenance, not a claim about causal model influence or complete history |
| Full owning-store local recovery | [Recovery receipt](be14-local-recovery-validation.json): bounded single-actor bundle, isolated reader drill, canonical reconstruction and crash rollback | Multi-actor, legacy/external stores, key custody and production cutover remain unqualified |
| Capture indexing/search/batch filing | [Capture receipt](be07-capture-completion-validation.json): bounded UTF-8/supplied extraction, lexical/fuzzy retrieval, reviewed reversible metadata CAS | No implicit OCR/PDF/image understanding, original-byte move, grant expansion or cross-database atomicity |
| Durable decline and day/week planning | [Agenda receipt](be12-agenda-decline-validation.json): explicit decline and fixed/flexible/overflow plan from retained input | No calendar mutation or reopening terminal/superseded obligations |
| Scheduled workflow production | [Scheduled-draft receipt](be12-workflow-production-validation.json): exact approved Markdown workflow, bounded grants/debits, private immutable draft and fresh publication approval | Broader scheduled models/scripts/domain producers are unsupported; notification retry cannot rerun work |
| Configured speech and strict accounting | [Configured adapter](be13-speech-validation.json), [strict accounting](be13-strict-speech-validation.json): finite local Whisper/Piper bridge, genuine admission, shared budget and uncertain-debt retention | Installed model/package, live audio, hardware permissions and supported host qualification are still required; streaming speech is not implemented |
| Connected read bridges | [Connected sources](be10-connected-sources-validation.json), [exact prepared review](be10-source-preview-validation.json): account-pinned Gmail thread/Calendar availability, retained original plus projection and exact prepublication bytes | Gateway account-pin support and live credentials are unqualified; no fallback to an unpinned account, general inbox or arbitrary provider tool |
| Operator repair/privacy interface | [Operator RPC receipt](be14-operator-rpc-validation.json): existing broker-controlled preview/apply, audit/retention/checkpoint and deletion plans | No arbitrary repairs, complete erasure claim or unknown personal-harness mutation |
| Named configured specialist handoff | [Specialist receipt](be13-named-specialists-validation.json): actual command/lease/budget admission and existing delegated-agent SDK path, isolated built-in memory, pinned methods and human review | Live provider behavior and full team coordination value remain unqualified; no automatic acceptance/continuation |
| On-demand project opportunities | [Opportunity receipt](be11-opportunity-validation.json): deterministic selected-project evidence, persisted revision dedup and save/dismiss/accept | No unsolicited activation, model-generated certainty, automatic task launch or action approval |

The frontend's matching controls have separate source-bounded receipts. A backend
method alone does not establish a usable client flow. See
[frontend scope](frontend-release-scope.md) and the [final FE13 follow-up receipt](fe13-followup-validation.json).

## Explicitly unresolved or conditional

- Primary Ryoko's external memory harness remains the sole intended personal-memory
  authority. Unknown deployed mutation/export/delete schemas are not invented;
  unavailable operations stay unavailable. The configured read adapter's offline
  evidence is not a live service qualification.
- Other agents retain isolated built-in memory and explicit project grants.
  Selecting a specialist never grants primary personal-memory access.
- Full teams and unsolicited opportunity experiments remain disabled pending their
  measured-value gates. LAYA remains off/shadow as configured; no training,
  promotion or rollout decision is authorized by these receipts.
- General browser consequential actions, cross-device authenticated routing,
  external channel breadth and external writes are not certified by finite local
  executor or connected-read tests.
- Native Electron/audio, supported OS/provider matrices, browser visual acceptance,
  live source quality and matched human-review/time/cost pilots remain open.
- Dots is a separate future consumer. Producer contracts do not establish OD00
  compatibility, dual-runtime cutover safety or a deployed Dots adapter.

## Validation and publication

The [second full campaign](be18-second-campaign-validation.json) retains its
`009255ec` source, failures, skips and later bounded reruns. The [final full campaign](be18-final-source-validation.json) against the candidate
above completed with 56,979 passed, 219 failed and 724 skipped across 5,356 files.
Twelve modules exited nonzero despite passing tests and fifteen had no tests run;
setup/teardown outcomes remain in the complete [test index](be18-final-test-index.json).
Separate fixture corrections passed 260, 73 and 16 tests; these overlapping reruns
do not rewrite or sum into the original campaign. The selected 29-file authority
group passed 357 tests within the campaign; explicit release slices/tooling/parity
passed 14 tests and all 196 changed Python files passed Ruff. The
[current manifest](release-manifest.json) remains blocked by the full-suite gate.
Neither targeted passes nor the frontend aggregate establishes release signoff.

Remote `main` was last exactly verified through BE14
`283163a719c25666c36a35da3fd513bb7d7aa532`. Later implementation and journal
checkpoints are local only while normal Git authentication is unavailable.
The user requested no Git-blob publication overnight. No CI-green, publication,
live deployment or cutover claim follows from these local checkpoints.
