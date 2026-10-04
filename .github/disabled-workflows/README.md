# CI policy and disabled GitHub Actions

**NEVER USE CI.** Do not create, enable, re-enable, dispatch, or rerun CI or GitHub Actions workflows unless the repository owner explicitly changes this policy. When the owner asks to test and verify, run the relevant checks directly in the selected development/test environment and report the actual commands and results.

## Deactivated on this branch

On 2026-10-04, all 52 GitHub Actions workflow definitions were moved from `.github/workflows/` into this directory, with `.disabled` appended to each filename. This includes build/test/lint CI, security scans and review gates, release and deployment workflows, publication, and scheduled maintenance. No executable workflow definitions remain on this branch. Originals are preserved byte-for-byte under the original Git blob IDs listed below.

The initial main-branch change disabled four independent workflows. After the owner explicitly approved stopping the remaining security, release, deployment, and publication workflows, all 52 definitions were archived on this build branch. App source, runtime security controls, dependencies, tests, and local scripts are unchanged. Disabling hosted security scans is not a replacement security implementation or evidence that the app is secure.

## Scope and limits

This is a source-level deactivation on this build branch, not a change to repository/account Actions settings. Older branches and tags can retain workflow definitions; updates or manual dispatches against old refs may still run them. Existing open pull requests may also retain workflow definitions. Existing runs are not canceled by these file moves. Required-check and branch-protection settings were not changed and may leave merges waiting for checks that will no longer run. Logs, artifacts, release history, credentials, permissions, Dependabot configuration, and app security code were not removed or changed.

No CI was dispatched or rerun to verify this change. Verification is by remote commit/tree readback, absence of active main-branch workflow files, exact preservation of all 52 original blobs, and read-only queued/running Actions checks.

## Direct local validation

Use the checkout's existing developer guidance, prepared toolchains, and an isolated development `HERMES_HOME` / `HERMES_RUNTIME_DIR`. These are available validation paths, not claims that any test suite or audit ran during the workflow-disable change. Run the checks relevant to the requested task and report the results directly.

Sources: [`CONTRIBUTING.md`](../../CONTRIBUTING.md#development-setup), [`AGENTS.md`](../../AGENTS.md#testing-applies-everywhere), and root [`package.json`](../../package.json).

1. Prepare the checkout's PM environment: `source ./activate` in Bash, or `. .\activate.ps1` in PowerShell. Python must be the prepared pinned interpreter; do not modify a live app environment.
2. Build a separate test interpreter in a fresh, nonexistent destination: `python -m pm.build_env --source . --out .venv --group dev --group test`. Do not remove an existing environment as part of this command. For an external test environment, set `HERMES_PYTHON` as described in CONTRIBUTING.
3. Run focused Python checks: `scripts/run_tests.sh tests/agent/ -v` is the documented example; choose the relevant existing test paths for the changed component. Run the full suite with `scripts/run_tests.sh` when warranted. Use this runner rather than bare pytest; on Windows run it through Bash.
4. Prepare root JS workspaces with `npm ci`, then run `npm run check` (the root script dispatches workspace checks), or the relevant workspace checks. These run directly in the chosen environment; the word `ci` in `npm ci` names npm's lockfile-based installer, not GitHub Actions.
5. For website changes: `npm ci --prefix website` followed by `npm run build:fast --prefix website`.
6. Existing read-only dependency audit scripts: `npm run audit:root`, `npm run audit:web`, and `npm run audit:tui`. Assess findings and validate fixes directly; do not use the `audit:fix:*` scripts without an authorized dependency-change task.
7. Manually exercise the changed path with the checkout's `hermes` command as CONTRIBUTING describes. Native install/update and OS-specific tests need a suitable disposable environment, never the user's live app. Do not dispatch the archived Actions to obtain platform coverage.

Retain the existing security policy and runtime protections in [`SECURITY.md`](../../SECURITY.md). Security-sensitive changes still need direct review and relevant local validation; this deactivation does not redesign those controls.

## Original workflow manifest

- `archive-inputs.yml.disabled` — original `.github/workflows/archive-inputs.yml`, blob `1ec41e6ff5e0f735ac99429b92d24a0539a1d447`
- `bootstrap-installer-build.yml.disabled` — original `.github/workflows/bootstrap-installer-build.yml`, blob `dea862f675547970bddfffb34cd0b194dc3cb1c9`
- `bootstrap-installer.yml.disabled` — original `.github/workflows/bootstrap-installer.yml`, blob `127f6c7a53ba545920ebce7432cd1ee8179cdd2c`
- `canary-release.yml.disabled` — original `.github/workflows/canary-release.yml`, blob `6c6278190a64d843233ea81c2e228b5d61f6adc1`
- `case-collision-check.yml.disabled` — original `.github/workflows/case-collision-check.yml`, blob `b4dfe03f807a32a591f6df00b907146673883b84`
- `ci-review-comment.yml.disabled` — original `.github/workflows/ci-review-comment.yml`, blob `c456ab520bf0708b11de0edbe2f27a1858499dad`
- `ci.yaml.disabled` — original `.github/workflows/ci.yaml`, blob `adfb6161888e592b384b25427944cdc53bb8b69e`
- `contributor-check.yml.disabled` — original `.github/workflows/contributor-check.yml`, blob `c6ccaf3ac782db5959166dc3a7f2e3e6e162c056`
- `deploy-site.yml.disabled` — original `.github/workflows/deploy-site.yml`, blob `c46850afd69db771a7aac8061034cf194e3d086c`
- `desktop-bundle-smoke.yml.disabled` — original `.github/workflows/desktop-bundle-smoke.yml`, blob `df397404b476e6b6b1e4c3e2ab66f6598bdc8ca6`
- `desktop-bundled-release.yml.disabled` — original `.github/workflows/desktop-bundled-release.yml`, blob `db3f04b6956e4f13b1cf2dfea1d5584aa178bcb4`
- `docker-lint.yml.disabled` — original `.github/workflows/docker-lint.yml`, blob `4aae36c82e54f5d51879880b40090bc0bbb15a14`
- `docker.yml.disabled` — original `.github/workflows/docker.yml`, blob `c404f9be69459e152ccaa8206d4e108e810442bd`
- `docs-site-checks.yml.disabled` — original `.github/workflows/docs-site-checks.yml`, blob `d502e2e328179a2f190419bd0bf7c7f78a30189d`
- `e2e-desktop-core.yml.disabled` — original `.github/workflows/e2e-desktop-core.yml`, blob `16b9b329c21c26f53d111e0ec7fb0a73a992b22a`
- `e2e-desktop-update.yml.disabled` — original `.github/workflows/e2e-desktop-update.yml`, blob `68ce12f3c50c0b24bd220c6dd48ab93bc377f605`
- `e2e-desktop.yml.disabled` — original `.github/workflows/e2e-desktop.yml`, blob `8583fe7384b4648f968b23a8a013946a4a2724a0`
- `history-check.yml.disabled` — original `.github/workflows/history-check.yml`, blob `8ba149e60e888a06b27c950eb5eed701093fb5a4`
- `icons-freshness-check.yml.disabled` — original `.github/workflows/icons-freshness-check.yml`, blob `1f451b013857430e1ee4516ef793aa9158eb54f8`
- `infographic-check.yml.disabled` — original `.github/workflows/infographic-check.yml`, blob `eb01e7be1521279d5009f1d17a45db02415d3d14`
- `install-e2e-macos-run.yml.disabled` — original `.github/workflows/install-e2e-macos-run.yml`, blob `4a558672c8d725c33fcb9f6bd4bc1ebd8556213b`
- `install-e2e-red.yml.disabled` — original `.github/workflows/install-e2e-red.yml`, blob `a7e6d952246d83ec925e361fae523a5db6a13577`
- `install-e2e-run.yml.disabled` — original `.github/workflows/install-e2e-run.yml`, blob `97a27579e1fdc0ee6f5dcf51d17d9feef9aabfc2`
- `install-e2e-windows-run.yml.disabled` — original `.github/workflows/install-e2e-windows-run.yml`, blob `64dafff079aa614d0c75de417a631d5640315950`
- `install-e2e.yml.disabled` — original `.github/workflows/install-e2e.yml`, blob `012ad27019cd7cc58799f05fa57a8107f82869b2`
- `js-autofix.yml.disabled` — original `.github/workflows/js-autofix.yml`, blob `3ec1075c0e9d96b3d812290e2b82b984f56a8216`
- `js-tests.yml.disabled` — original `.github/workflows/js-tests.yml`, blob `252b135f276f300c0d05417ad914b2c0387d4383`
- `label-rerun.yml.disabled` — original `.github/workflows/label-rerun.yml`, blob `d8d6fe79059c7665c042f105e97976496d55d335`
- `lazy-deps-guard.yml.disabled` — original `.github/workflows/lazy-deps-guard.yml`, blob `a78fae82c96223859dac142ec48e6bf0197222fe`
- `lint.yml.disabled` — original `.github/workflows/lint.yml`, blob `bfa41ef848b1f6e949c3ebd3d54a09b91f6f75cf`
- `live-providers.yml.disabled` — original `.github/workflows/live-providers.yml`, blob `4fc491886d65e9c8c15a3c68da1c8af600b1558d`
- `lockfile-diff.yml.disabled` — original `.github/workflows/lockfile-diff.yml`, blob `bed10759ca18e1e2813a1d767d7406ea7d1ed0c8`
- `nix.yml.disabled` — original `.github/workflows/nix.yml`, blob `fae453b64791d31e6ad7d28cdfc29ed19cf0d1dd`
- `osv-scanner.yml.disabled` — original `.github/workflows/osv-scanner.yml`, blob `11928250d5d0a4a298bad941b43ceafb8fd94dd0`
- `plugin-catalog-ci.yml.disabled` — original `.github/workflows/plugin-catalog-ci.yml`, blob `72f1b642c94692c1b992b857d9b0b0ed241598fd`
- `pm-bundle.yml.disabled` — original `.github/workflows/pm-bundle.yml`, blob `79c921437b0b2df182cb07c0ce8b9182030e2a5b`
- `profile-artifact-check.yml.disabled` — original `.github/workflows/profile-artifact-check.yml`, blob `e444b18e31e8a96f769f82dff1ea1dec211a8673`
- `review-labels.yml.disabled` — original `.github/workflows/review-labels.yml`, blob `b54cb1cc56b0ca8e3d8a3bd1d74c9cd3a75e74e6`
- `rust-tests.yml.disabled` — original `.github/workflows/rust-tests.yml`, blob `0c4c4e79fcca5fc0e55876b3403f8c862be8f1e7`
- `sandbox-image.yml.disabled` — original `.github/workflows/sandbox-image.yml`, blob `de888e6ef062ac833e38cda9be79c45856bf9dad`
- `skills-index-freshness.yml.disabled` — original `.github/workflows/skills-index-freshness.yml`, blob `dcfcc5a0dd2d0650b2c7b4b0753e55837e11bad6`
- `skills-index.yml.disabled` — original `.github/workflows/skills-index.yml`, blob `6fed6e292f26effd97ebbb4faf8d4b70db38ed83`
- `stable-release-publication.yml.disabled` — original `.github/workflows/stable-release-publication.yml`, blob `10288a0d7ca279e3eaf5f0c2b5153d19bde3fbd7`
- `stable-release.yml.disabled` — original `.github/workflows/stable-release.yml`, blob `727f9ac72e8366fe16057f77e05e88145a688598`
- `supply-chain-audit.yml.disabled` — original `.github/workflows/supply-chain-audit.yml`, blob `15084aa53f9607305de5adfbf598480e3e7b4222`
- `termux-verify.yml.disabled` — original `.github/workflows/termux-verify.yml`, blob `cf5429067c1f575db8d647a6dd3b08b80592a84e`
- `tests-os.yml.disabled` — original `.github/workflows/tests-os.yml`, blob `451003c9adee137c5276639db3e6989c739bf9d1`
- `tests.yml.disabled` — original `.github/workflows/tests.yml`, blob `98a103c22b54d101f0816ffa2f57fc1e42abb3f2`
- `uv-lockfile-check.yml.disabled` — original `.github/workflows/uv-lockfile-check.yml`, blob `8daaab9c007d09c13df8c508dfaa8513f8fef86d`
- `windows-bundle-sdk.yml.disabled` — original `.github/workflows/windows-bundle-sdk.yml`, blob `47276151b831be3d8e84cfcf0601ed6fd78407ca`
- `windows-install-update-e2e.yml.disabled` — original `.github/workflows/windows-install-update-e2e.yml`, blob `ec1180a612558abae329502aab08e11a9dab2b2e`
- `windows-venv-e2e.yml.disabled` — original `.github/workflows/windows-venv-e2e.yml`, blob `33bf7ef72d64384f2694123c3b567f5a572d468b`

## Restoration

Only if the owner explicitly changes the no-CI/no-Actions policy: restore the specifically approved workflow files and their required reusable-workflow dependencies to `.github/workflows/`, removing only the final `.disabled` suffix; update this policy and AGENTS.md in the same change. Use the manifest to verify exact contents. Restoring a workflow may immediately resume its push, pull-request, scheduled, release, or other triggers. Never restore or rerun workflows merely to verify this deactivation.
