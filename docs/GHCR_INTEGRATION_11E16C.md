# 11E-16C unified readiness and bootstrap preparation

Parent 1: PR #32 1bd1f7970aa11361bb0c5101b6107178fdb5a8d5 (11/11 bootstrap tests and conventional CI passed).
Parent 2: PR #31 f8d452b4f4b7009f914a52b1980ee785dfec81eb (conventional CI passed; real read-only metadata returned 404 twice).
Main baseline b58540d294d93250ae914a1f670b423ec39daf59. Both PR histories are retained. No overlap in original additions.
The exact diagnostic workflow and bootstrap execution workflow are unchanged.
Only CI trigger scope and offline diagnostic tests are added for integration.
The application Docker CI caller includes integration/ghcr-11e16c; it retains
the existing six mandatory application jobs and no registry write permissions.
Bootstrap safety tests run on the isolated integration branch and PR; the new
tests execute the actual inline diagnostic Python using fake HTTPS responses.
No real GHCR API, token, package initialization, registry push or deployment
in the integrated tests. A 404 stays blocked; never convert operational
readiness failure into success. Offline tests verify both packages are checked,
HTTP failure, bad metadata, visibility, version-list errors, missing/wrong
link not proving write, redirection rejection and sanitized output.

The existing publisher, fixed application SHA 1a87f65 and CI 45633909294,
Dockerfiles, vulnerability policy, ACL tests, main deployment protection and
all actual package write gates stay unchanged. Only the main manual bootstrap
entry has packages:write. Account owner must independently confirm both
packages absent and no external writer before separately approved execution.
Do not merge main, initialize, publish, deploy or modify production data
in this phase. Actual package access and registry response behavior remain
unverified until a separately authorized run.
