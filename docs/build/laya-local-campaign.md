# LAYA assembled local campaign

Recorded 2026-10-05. **Local code campaign passed; real release qualification is blocked.**

- Published candidate: [`1a3ec3dc6a2edab5a52702c568ce28bb98640f0c`](https://github.com/TianJieHeng/ryoko-agent/commit/1a3ec3dc6a2edab5a52702c568ce28bb98640f0c)
- Exact tree: `2da7a0f2ee88e35da2650d1291e637b55bd20797`
- The tests ran on local commit `95ebe8bf666e4617ff114267b64a19bf640059c9`; publication verified the identical complete tree
- **1,267 passed, zero failed/skipped/retried, 69 files, 127.7 seconds** with four local workers
- [Exact arguments, environment and per-file results](laya-local-campaign.json); [runner output](laya-local-campaign.log)

The interpreter was the existing Python 3.14.7 test environment. Invocation used
`HERMES_PYTHON` pointing to that environment, `HERMES_TEST_FILE_RETRIES=0`, and
`scripts/run_tests.sh -j 4` followed by every test path in the manifest's `argv`.
The canonical runner scrubs credentials and isolates home/runtime and per-file
processes. The recorded output replaces only its absolute interpreter path with
its role/version. No CI or paid compute was used.

## Coverage and review

The selected campaign covers all decision/LAYA tests, actual owner requests,
identity/lifecycle, profile and secret isolation, catalog/tool discovery and
bridge dispatch, budget accounting/replay, runtime journal and leases,
compression/cached prefix ownership, generated contracts and runtime RPC.
It includes synthetic native three-stage decisions, local hostname-verified TLS,
real SQLite journal transactions, A→B→A profiles, rollback/revocation, cancellation,
late results, uncertain completion, blocked storage, and restart restoration.

Independent focused review closed four findings before the campaign: default-off
multipart containment, exact frozen-prefix restoration after failed reads,
per-attempt uncertainty attribution, and bounded receipt publication without a
lifecycle lock held over I/O. The original blocking-lock defect was reproduced
with the new regression, then the corrected version was restored and passed.

Additional local checks passed:

- Configured Ruff rules on every Python file changed since the integration base
- `node node_modules/typescript/bin/tsc -p apps/shared --noEmit` using existing TypeScript 6.0.3
- Generated Python/TypeScript/OpenRPC checks, included in the 69-file campaign
- `git diff --check`
- Installed-distribution base requirements: 135 checked, none unsatisfied. The interpreter has no pip module; this used `importlib.metadata` and the installed `packaging` requirement parser without installing anything
- Credential-signature review of 62 changed tracked files: one finding was an explicitly synthetic redaction-test token. This is a bounded signature check, not comprehensive secret or credential-custody certification

## Qualification is still blocked

The [candidate inspector exercise](laya-candidate-qualification.json) has all
production/L07/L09/L10 flags false and 35 blockers. It reruns the committed
`evals/decisions/laya_qualification_fixtures.json` with **only** `candidate_sha`
changed to the published candidate above, through
`python -m evals.decisions.qualify_laya <temporary-fixture-copy> --readiness docs/build/laya-integration-readiness.json --output <new-report>`.
Its outcome and accounting numbers are synthetic fixture data, not measurements
of this candidate with the real model. The source fixture remains unchanged.

No real-model shadow collection or independently labeled paired task campaign
was performed. Actual model/checkpoint identity, auth/routes, privacy/custody,
existing-hardware cold/warm/load/outage/boot behavior, calibration, task quality,
cache-inclusive savings and latency remain unqualified. Smaller schemas and
passing software tests do not prove a net benefit. No private traffic was sent.

This is a Linux cloud campaign, not the approximately 39,000-test full repository
suite or actual Ryoko/Jetson/macOS/Windows host validation. No vulnerability
service/database scan was performed. A genuine SQLite/filesystem commit stall
can still obstruct the shared store. See [readiness](laya-integration-readiness.json)
and [operator requirements](laya-runtime-qualification.md) before any promotion.
