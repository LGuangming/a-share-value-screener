# 11E-15 — reviewed GHCR test pair (PR #28)

Source lock: `cf928feb8463abf240356dbb381a6348fc716902` (PR #28), successful CI run
[45630877479](https://github.com/LGuangming/a-share-value-screener/actions/runs/45630877479),
six core jobs including independent release candidate acceptance. This PR
does not merge source PRs, publish images or deploy. Review push/PR events
have only read permissions. The workflow explicitly verifies the historical
run, exact six successful jobs and each checkout's HEAD before building.

## Prepublication acceptance (same pair, no rebuild)

Build both linux/amd64 images on one disposable runner from the reviewed SHA,
with the exact committed Node/Python/Web/Minimal base digests, wheel hashes
and default Minimal target. OCI source/revision/version labels, actual
non-root UID/GID, shell-free Web/Worker runtime, architecture and credential
environment exclusions are checked. Reuse the accepted PR28 runtime checks,
SQLite/Expat checks, PG18.7 isolated Compose/ACL/Parquet/guard/recreation
checks, both Minimal Trivy generations and Web Trivy 0.76.2, secret gates,
SBOMs and raw vulnerability evidence. No production DB or archive.

Use the exact reviewed source's candidate_vulnerability_gate.py (not a
second independently drifting allowlist). Only HIGH CVE-2026-107494 and
CVE-2026-124709 in Debian 13 libssl3t64 3.5.9-1~deb13u1, without fixed
version, are accepted until 2026-12-26 00:00 UTC. Every other HIGH/CRITICAL,
secret, expired exception, scanner/evidence failure remains blocking.
Both images must pass before any publication job can start. Preserve
the actual accepted image config IDs, raw JSON decisions and SHA-256 of the
docker-save archive; test local save/load on review as well as dispatch.
Review uploads evidence only, never the image archive or registry credentials.
Only explicit dispatch transfers the compressed accepted pair as a same-run,
same-attempt one-day artifact; no rebuilding in the write-permission job.
That job checks receipt SHA/run/attempt, hashes, loaded image IDs, labels,
architecture and UID/GID, then re-evaluates the same three raw reports against
the current clock using the pinned policy. No scan expiry override.
Historical CI local config IDs need not equal the newly rebuilt images:
the new pair must pass its own acceptance; deployment must use its verified
registry digests, not historical CI local IDs or mutable tags.

## Future explicit authorization (NOT executed in 11E-15)

GitHub requires workflow_dispatch on default branch. Review Vercel Git
integration before separately authorized main merge of PR #27. Do not
dispatch prior PR26 workflow. Future manual main dispatch requires
`PUBLISH-PR28-TEST`. The only two packages are:
- ghcr.io/lguangming/a-share-value-screener-web-test-pr28
- ghcr.io/lguangming/a-share-value-screener-worker-test-pr28

**Private-package prerequisite:** both exact packages must already be
confirmed private by the GitHub package API with repository write access
before any push. Missing/404, inaccessible, unknown visibility or public
packages block (no assumed privacy, no automatic first-push/bootstrap).
If absent, separately authorize a safe private-package provisioning plan.
This is an intentional fail-closed choice; package setup, access and API
behavior have not been verified in 11E-15. Do not change visibility or
create packages in this phase. No production package name or latest tag.
Use unique source SHA + run ID/attempt tags; check both tags absent before
either push. Publication uses a separate job, packages:write only there;
short-lived GITHUB_TOKEN is supplied only to explicit package steps.
Recheck each pushed tag's manifest digest independently; authenticated
digest pull must reproduce accepted config ID, platform and source revision.
Anonymous access must be explicitly denied; ambiguous errors fail closed.
Recheck both package visibilities; issue complete pair-release.json only
after both images and all checks succeed. GitHub registry pushes are NOT
atomic. If first succeeds and second fails, keep the partial result
quarantined, do not deploy either, do not delete/overwrite without separate
approval. Review package visibility and digest pair in GitHub UI before
a separately approved VPS test. No SSH, deployment, production data or
scheduler changes. Registry manifest digests differ from local config IDs.

Review CI must succeed on this PR #27 SHA before publication is considered.
The dispatch-only artifact transfer, registry login/push/pull and private
API visibility checks are unexecuted by design in review mode; no claim
that GHCR permissions or package existence have been validated.
