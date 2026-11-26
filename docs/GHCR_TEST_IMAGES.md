# 11E-15C — reviewed GHCR test pair (integrated application)

Source lock: `1a87f659f6e83d9d92d8f04cba5f3891bc769507` (integration/vps-11e15a), successful CI run
[45633909294](https://github.com/LGuangming/a-share-value-screener/actions/runs/45633909294),
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

## Read-only review trigger

The first 11E-15 commit 6d6f79a only showed Tests and Build at inspection;
the direct GHCR run was not yet observed. A reusable ghcr-reviewed-pair.yml
workflow now holds the two read-only acceptance jobs. Tests and Build
explicitly calls it on feature/ghcr-test-publish pushes. The normal PR and
future manual main wrapper call the identical reusable workflow. Neither
the reusable workflow nor the review caller has packages:write; only the
manual wrapper's dispatch-only publish-test job has that permission. No
secrets inherited. The wrapper and reusable workflow must retain identical
REVIEWED_SHA and REVIEWED_RUN; mismatch also fails the accepted-pair receipt
check before publication. No publish job exists in the push review path.

Historical-run API reconciliation: run 45630877479 is completed/success,
but the independent gate job endpoint reported status in_progress with
success conclusion and a recorded completed_at. Keep the top-level run
terminal check, exact six job names, every successful conclusion and
nonempty valid completion time bounded by the finished run, and require
the independent gate's exact step completed/success. The stale per-job
status is logged, not used to override a missing/failed conclusion or
completion timestamp. Initial review correctly stopped at the inconsistent
historical status and skipped image builds; revised review is required.

## 11E-15C — integrated source lock (operator approved 2026-11-25)

Supersede original PR28 source `cf928feb8463abf240356dbb381a6348fc716902` / run 45630877479
with integrated source `1a87f659f6e83d9d92d8f04cba5f3891bc769507` / run 45633909294.
The integration combines the complete PR20/21/22/23/26/28 chain with main
477ab29676740f42f429033b1d293c1ad7a2e654, preserving all ten obsolete
migration-file deletions and the main-only VPS read-only operations workflow.
All six integration jobs and independent acceptance succeeded. The original
PR28-based 11E-15 read-only run 45633027571 is historical evidence only.
Both wrapper and reusable workflow pin the new SHA/run; the latter also
checks the integration branch identity. No security, artifact handoff,
private package, identity, digest, or publication permission changes.
The existing test-pr28 namespace and PUBLISH-PR28-TEST confirmation are
retained to avoid changing the previously documented private-package contract;
the tag, OCI revision and pair receipt always identify the exact integrated SHA.
Require a fresh successful read-only pair review on this PR27 commit before
final main integration preparation. No publication, deployment, production
connection, main update, or change to the original integration branch.


## 11E-16D — failed publication recovery (draft preparation)

Run 45741563355: both builds, isolated functional tests, scans, save/load and
write-job revalidation passed. Web push returned
sha256:04d24373966809820ad6a5547eb3b16882f6969aa1c8ce5a08204256ca1fdab2.
Exit 255 immediately after push, before observed pull/Worker push. The prior
shell pipeline and temporary DOCKER_CONFIG are suspects, not proven causes.
No complete receipt. Do not delete or deploy either package.

The new publisher uses direct authenticated HTTPS GHCR pull-scope token and
manifest GET, with no redirects, bounded bodies, explicit error codes and
SHA-256 of returned bytes matched to Docker-Content-Digest. No Buildx or
early-exit awk pipeline in the write path. Both tag and immutable digest,
private linked package identity, pulled config ID/arch/UID/revision and
independent anonymous HTTP denial are required. Explicit MANIFEST_UNKNOWN
or NAME_UNKNOWN only permits new tag; mixed auth errors/unknown status block.
Record atomic publication-progress.json before each stage, including push
attempt and returned digest, upload even on failure. Raw command output and
tokens are not printed. Complete pair-release.json only after both verified
and final privacy/tag checks. No overwrite, delete, rollback or auto retry.
GitHub Actions write permission is only in main owner first-attempt dispatch
with exact confirmation; reviewed source checkout is separate from publisher
control checkout. Prior read-only review and scans are unchanged.

One-time recovery is deliberately pinned to failed run 45741563355, attempt 1,
artifact 15080908340 (accepted-pair-45741563355-a1, 179945528 bytes),
ZIP sha256:3ab4407eedf26621cc5bc8e40871254bfb0d5a181a1f5ece1d724f272617d7fd,
expiry 2026-11-27T02:24:43Z. No arbitrary run/artifact/digest inputs. Separate
owner approval + new main dispatch with RECOVER-PR28-45741563355; original
run/job/step provenance, artifact ID/repo/SHA/time/digest and actual ZIP bytes
checked before extraction; strict allowed filenames and complete checksums.
Re-evaluate all original raw vulnerability gates using pinned source at
current clock; retain existing Web only if its original tag AND immutable
digest equal the recorded push and authenticated pull matches accepted
image config, privacy, platform, UID, revision and anonymous denial. Require
Worker original tag absent before any write; only push accepted Worker,
same original tag, verify both. Receipt records current dispatch ID and
original accepted run/attempt. No rebuild in recovery or write job.
If original artifact expires, is unavailable, changes, or either tag is
ambiguous, stop; no silent fallback. A separately authorized normal
PUBLISH-PR28-TEST dispatch rebuilds/rechecks a new pair under new tags;
the incomplete old pair remains quarantined. Additional partial recovery
would require separate reviewed pins; never infer a new retained digest.

Offline CI tests fake Registry, HTTP fixtures, provenance, ZIP digest,
authorization, partial receipts, recovery and exact file checks. Actual
GHCR token/manifest behavior, artifact download and resumed push are not
executed in this draft. No publication, main merge or deployment authorized.
