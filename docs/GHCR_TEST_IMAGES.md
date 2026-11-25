# Phase 11E — reviewed GHCR test images

This workflow is independent of migration PRs #20–#23 and #26. It builds
the already tested exact PR #26 commit d5f1ed82ea4360cfc0619aeb6e6fa13752177aac.
No app checkout from main, production secrets, registry credentials other
than the short-lived job GITHUB_TOKEN, deploy hooks, VPS SSH, or production
tags are used.

## Review and trigger
A pull request changing the workflow runs two read-only build/scan jobs.
It never publishes a package. GitHub requires workflow_dispatch to exist
on the default branch. Obtain explicit approval before merging only the
workflow/documentation PR into main; check Vercel Git integration first
because a main commit may independently trigger a deployment. Existing
migration PRs remain unmerged. When enabled, manually select main and
enter PUBLISH-PR26-TEST. The workflow rejects any other confirmation/ref.

Two dedicated packages (not existing production package names):
- ghcr.io/lguangming/a-share-value-screener-web-test-pr26
- ghcr.io/lguangming/a-share-value-screener-worker-test-pr26

Each tag includes the full source SHA and run ID/attempt; no latest tag.
The package initially defaults to private on first publication; confirm
visibility and access permissions in GitHub Packages settings. The workflow
requires a successful authenticated digest pull and an explicit rejection
of anonymous access. These do not replace the UI visibility review.
If either matrix job fails, do not use the other image for deployment;
a partial push may already exist and must not be deleted without approval.

Before any push, check image architecture, runtime UID/GID, revision label,
credential environment-variable names, and offline non-root execution.
Trivy secret and HIGH/CRITICAL vulnerability scans are blocking (including
unfixed vulnerabilities); no ignore file or automatic bypass. A scanner
failure also blocks publication. Do not interpret a passing scan as proof
of complete security. Node/npm dependencies are locked, but base tags and
Python dependencies remain version ranges; each newly built image is
scanned and deployment must use the verified immutable digest, not a tag.
The workflow does not deploy or schedule anything. The next approved VPS
step may use a read:packages classic PAT for private pulls, entered locally
via password-stdin; never share the token in chat or commit it.
