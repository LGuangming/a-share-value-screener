# 11E-16A — GHCR read-only package readiness

The only allowed targets are the existing publication contract's two exact
user-scoped container packages, a-share-value-screener-web-test-pr28 and
a-share-value-screener-worker-test-pr28, owned by lguangming. The diagnostic
does not create or change any package, visibility, repository association,
access role, image, production data or deployment. It does not call the
publication workflow.

The independent workflow runs automatically on a push changing its own
workflow file on feature/ghcr-readiness-diagnostic, and can later be
manually run on main if separately merged. It uses only packages:read
and fixed HTTPS GET requests to the exact metadata endpoint used by the
publisher and each package's versions endpoint (one entry maximum).
No PAT, inherited secrets, docker login/push/pull, write permissions, checkout,
or deployment call. The response body is not logged; only selected
metadata fields, HTTP status and classification are included in the
sanitized report and step summary. Redirects and ambiguous errors fail closed.

Interpretation:
- HTTP 200 with matching identity, visibility private and a successful
  version listing proves metadata and version-list access under this
  repository's read token. An empty version list is not a proof of write access.
- 404 means missing OR inaccessible, not proof of nonexistence.
- 401/403/429/network/invalid metadata and public/internal visibility block.
  Both packages are checked even if one fails.
- Linked repository metadata does not prove Manage Actions access role.
  A repository can have a separately granted role without a linked repository.
  Check Settings > Manage Actions access for the target repository's Write
  or Admin role in the package UI. A read-only token cannot establish actual
  write capability; neither a successful diagnostic nor a package link
  authorizes publication. If package metadata is inaccessible with the
  Actions token, the existing publisher's same endpoint must not be assumed
  usable. No automatic first-push bootstrap.

The publication source and prior security gates are unchanged: exact
application 1a87f659f6e83d9d92d8f04cba5f3891bc769507 and CI 45633909294;
main baseline b58540d294d93250ae914a1f670b423ec39daf59. The existing
publish-test job still requires both packages to be confirmed private
before any push, and all other pinned acceptance and digest checks.
No publication or deployment approval is conveyed by this workflow.
