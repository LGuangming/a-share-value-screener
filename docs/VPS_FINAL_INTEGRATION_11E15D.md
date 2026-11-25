# 11E-15D — final unified integration, no merge or publication authorization

Application parent: `1a87f659f6e83d9d92d8f04cba5f3891bc769507` (integration/vps-11e15a), completed 6/6 CI run 45633909294.
GHCR workflow parent: `1282b4ae93f6ac469beb592fedae88f109bb2725` (PR #27), completed 4/4 read-only CI run 45635293320.
Main baseline: `477ab29676740f42f429033b1d293c1ad7a2e654`.

The unified branch combines both histories without changing any original branch. Use the application parent's full tree, copy exactly the three GHCR workflow/document blobs from PR #27, and reconcile only ci.yml. Preserve all ten retired migration-file deletions and main's VPS read-only workflow. No other application, Docker, tests, lock, policy, or production code changes. Both GHCR workflows still lock the accepted application SHA and CI run, not this workflow-only integration commit. OCI revision and pair-release receipt must identify the immutable application SHA.

## Final verification

On an integration/vps-11e15d push, Tests and Build must run eight actual jobs: the six application jobs (Python 3.13, PG18.7 local pipeline, PG18.7 Web, base/wheels, Web+Minimal, independent gate) and two additional read-only GHCR jobs (historical proof/base/wheel verification, exact pinned dual-image acceptance/Save-Load). All permissions limited to contents:read and actions:read as required; no registry write, inherited secrets, actual publication, or deployment. The application caller also runs on future main pushes (read-only full application CI); GHCR review caller remains restricted to the review and final integration branches. A separate PR run may skip push-only callers; use the final push run for complete acceptance. Keep the manual wrapper's main-only explicit publication gate and private-package prerequisites unchanged.

## Deployment protection

At preparation, vercel.json exactly matches main and sets git.deploymentEnabled=false. Read-only Vercel project list/detail for a-share-value-screener returned no connected Git link or deploy hooks in the returned link metadata. External automation and manual deployment paths are not exhaustively audited. Do not change project settings, dispatch VPS operations, trigger deploy hooks, publish images, or modify production data. Existing cloud workflow schedules remain unchanged; production workflows must not be manually dispatched. Review deployment settings again before any separately authorized main merge.

This is a draft unified PR covering application PRs #20, #21, #22, #23, #26, #28 and GHCR PR #27. Do not close originals or merge main until the final eight-job run and review succeed and the operator explicitly authorizes it. Actual GHCR publication and VPS deployment are separate later authorizations; the two OpenSSL exceptions remain limited and expire 2026-12-26 00:00 UTC.
