# 11E-16C-5 — private GHCR initialization recovery

Supersedes the initial Bootstrap instructions (run 45649612640 failed after
the Web scratch push: package not confirmed private; Worker not pushed).
The log did not record the actual visibility, so root cause is not proven.
Owner reports deleting the Web package; recheck both targets in owner UI.
No production images were uploaded by that run.

GitHub documents private first publication and separate visibility vs access.
CLI push using a classic PAT without a source label is not linked by default.
See https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
and https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility.
This does not guarantee the registry will return private; stop on any deviation.

## Phase 1 — owner-local, unlinked seed (separate approval only)

Requires Git, Python 3.10+ and a local Linux Docker engine / Docker Desktop
with linux/amd64 build support, standard Docker buildx. Fetch approved main
after merge, checkout a clean main identical to origin/main. Do NOT run on CI,
do NOT add a personal PAT to GitHub Actions or send it in chat. Owner creates
a short-lived classic PAT with write:packages only (avoid repo and delete).
Confirm each absent target in authenticated owner Packages UI and no other
writer or formal publisher is active; no global lock across local CLI and CI.

Example from freshly fetched main, on a machine with Docker:
```
python tests/ghcr_private_bootstrap.py owner-seed --web absent --worker absent --receipt ../ghcr-owner-receipt.json
```
Type BOOTSTRAP-UNLINKED-PRIVATE-GHCR and enter PAT via hidden prompt.
Both exact names are fixed. New tag is UUID-based bootstrap-owner-*;
no app source label, no provenance/SBOM attestation, scratch one-layer exact
marker only. Both metadata checked before write; owner identity checked by
GET /user; 404 plus owner attestation, explicit tag absence, prepush recheck.
After each push require matching identity, private, unlinked, version digest,
remote digest, pulled scratch payload/config and anonymous denial. If status
public/internal/missing/unrecognized, save sanitized value and stop immediately.
No automatic permission changes/deletion/retry. Revoke temporary PAT after use.

If one side already exists privately, preserve it; provide its exact prior
bootstrap digest instead of absent (e.g. --web sha256:<64 hex> --worker absent).
Both existing private markers may be verified without writes; any existing
public/unknown/wrong association, invalid marker or ambiguous state blocks
before any new push. Existing marker must be in version list and pass
immutable pull, exact marker archive and anonymous-denial checks; no tag
overwrite. Receipt records partial state and exact pin, never secrets.
If receipt lost, owner must inspect exact marker digest in package UI and
independently confirm it is a bootstrap version. Do not use formal digests.
Deleted-name reuse and actual visibility remain provider-dependent.
 
## Phase 2 — link only after both private

In each Package settings, check Private; Connect repository
LGuangming/a-share-value-screener; disable inherited access and explicitly grant
that repo Write/Admin via Manage Actions access. Reconfirm Private after
linking. No automated visibility/permission change. Then manually dispatch
ghcr-package-bootstrap.yml on main with VERIFY-PRIVATE-GHCR,
owner_confirmed_access=true, and exact web_digest / worker_digest.
This workflow now has packages:read only, no initial push path, shares the
publisher concurrency lock. Actual read and anonymous-denial verified;
owner's UI access-role attestation is not proof of write authorization.
Run separate ghcr-package-readiness.yml as before. Do not run formal publisher
until both pass and separate release approval. The existing formal publisher,
fixed application revision/run, all scanning, private and dual digest gates
are untouched. No deployment allowed.

The test workflow uses fake registry and local scratch-only builds; no
actual push, API token, remote pull or deployment in test CI. 
