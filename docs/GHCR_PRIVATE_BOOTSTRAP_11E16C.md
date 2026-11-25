# 11E-16C — private GHCR bootstrap preparation only

Independent manual-only workflow for the two existing test package names.
No formal image publishing, no deployment, no production data changes.
The existing publisher, fixed source 1a87f65 / run 45633909294, all security
gates and existing private-package prerequisites remain byte-for-byte unchanged.

Prerequisites for a separately authorized future run: account owner
LGuangming verifies in authenticated Packages UI that both exact packages
are absent (404 is NOT sufficient), no external writer is active, and
reviews GitHub's first-publish private default. Only main workflow_dispatch
with BOOTSTRAP-PRIVATE-GHCR and owner_confirmed_absent=true, actor and
triggering actor both owner, first attempt only. Shared concurrency with
existing explicit publisher, no fork/PR/push trigger. Only bootstrap job
has contents:read and packages:write; tests have contents:read only.

Preflight GET both metadata endpoints, require 404 plus owner attestation.
Authenticated exact run-specific tag manifest check must conclusively
report absence; auth/network/ambiguous failures block. If GHCR masks
nonexistent tags as unauthorized, STOP: no workaround or first push
without separate review. Refuse existing metadata even if private.
Recheck each target immediately before push. These checks cannot
eliminate an external race, hence owner must confirm no concurrent writer.
Never overwrite, delete, change visibility or grant permission automatically.

Build isolated local scratch marker, no repository files in build context,
no application/config/secret, network none, linux/amd64. Verify one layer
and actual Docker-save payload contains only exact BOOTSTRAP_ONLY marker.
No runtime command or environment except the exact standard Docker PATH.
Push only unique bootstrap-run-ID-a1,
never latest or a production release tag. Immediately verify matching
private package and expected repository association, remote manifest
digest, authenticated pull config ID, anonymous denial and version read.
Recheck both private metadata at end. If any step fails, do not continue
to next push; retain partial receipt, no cleanup of remote packages.
If first push succeeded but metadata not accessible, block and request
owner review. No automatic rerun. Local credential directory is temporary.

A successful bootstrap receipt is NOT pair-release.json, does not
authorize deployment or formal release, and is not a runtime image.
Operator still checks private visibility, inherited access and Manage
Actions access (Write/Admin for target repository) in package UI. A successful
push establishes that specific run's write access, not permanent permission.
Re-run independent read-only readiness diagnostic, then seek separate
formal release approval. Remove or disable bootstrap entry after success
under separate approval. Do not change the formal publish workflow.

Tests use mock registry and local-only Docker scratch build, inspect and
save; no login, HTTP package calls, remote pull or push. CI on diagnostic
branch/PR is read-only. This change is not authorized for main merge or
actual initialization.
