# CI policy and disabled workflows

**NEVER USE CI.** Do not create, enable, re-enable, dispatch, or rerun CI unless the repository owner explicitly changes this policy. Use local checks when needed.

## Disabled on main

On 2026-10-04, the following four independent CI/CI-support workflows were moved out of `.github/workflows`. Their original contents and Git blob IDs are preserved; the `.disabled` files cannot be discovered as GitHub Actions workflow definitions here.

- `js-autofix.yml` -> `js-autofix.yml.disabled` (original blob `3ec1075c0e9d96b3d812290e2b82b984f56a8216`)
- `live-providers.yml` -> `live-providers.yml.disabled` (original blob `4fc491886d65e9c8c15a3c68da1c8af600b1558d`)
- `windows-bundle-sdk.yml` -> `windows-bundle-sdk.yml.disabled` (original blob `47276151b831be3d8e84cfcf0601ed6fd78407ca`)
- `install-e2e-red.yml` -> `install-e2e-red.yml.disabled` (original blob `a7e6d952246d83ec925e361fae523a5db6a13577`)

## Scope and remaining automation

This is a partial deactivation: 4 of the 52 workflow files on main were moved; 48 remain unchanged. It does not disable GitHub Actions at the repository/account level, change other branches, alter branch protection or required checks, or cancel old runs.

- `ci.yaml` still handles pull requests and pushes to main. It combines ordinary test/lint/build lanes with `supply-chain-audit.yml` and `review-labels.yml`, and `stable-release.yml` calls it. Its reusable lanes remain in place to avoid broken security/release calls.
- `stable-release.yml` also calls Nix, PM bundle, Termux, Windows and install/update E2E workflows. Those coupled CI workflows remain unchanged pending the owner's scope decision.
- `docker.yml` and `sandbox-image.yml` combine build/test work with container publication. Release, deployment and artifact-publication workflows remain unchanged.
- `plugin-catalog-ci.yml` includes supply-chain admission checks; `osv-scanner.yml` is a security scanner. Security automation and its CI review-comment/label-rerun support remain unchanged.
- Skills-index publication and freshness monitoring remain unchanged.

The owner's no-CI directive applies despite these preserved definitions. Do not interpret their presence or older development-guide examples as permission to run CI. Completing shutdown of the coupled workflows requires an explicit decision about their security and release effects.

## Restoration

Only after the owner explicitly changes the no-CI policy: move each selected `.disabled` file back into `.github/workflows/` under its original filename, removing only the final `.disabled` suffix, and update this policy documentation in the same reviewed change. The unchanged blob IDs above allow exact restoration. Do not restore or rerun workflows as a verification step.
