# Phase 11E-1 — image security remediation candidate

Stacked only on reviewed PR #26 (d5f1ed82ea4360cfc0619aeb6e6fa13752177aac). No change to PR #27, production,
registry publishing, VPS or main. PR checks use contents: read only.

Prior scan (PR #27 run 45577109938) reported Web 49 (48 HIGH/1 CRITICAL)
and Worker 106 (104 HIGH/2 CRITICAL) findings, primarily Debian Bookworm,
including zlib1g CVE-2023-45853 and libsqlite3-0 CVE-2025-7458.
Counts are package findings, not unique CVEs. The prior scan blocked publication.

This candidate retains Node 22, Python 3.11, glibc, non-root UIDs and
application behavior. Candidate images: node:22-trixie-slim and
python:3.11-slim-trixie. CI resolves the two registry manifests once per
workflow run, records the actual tag@sha256 references as an artifact,
then supplies the same immutable refs to both PostgreSQL test matrices.
Dockerfile defaults remain candidate tags until actual candidate digest
and security results are reviewed; **permanent digest pinning requires
a follow-up change**. Do not use these tags for a final release.
No invented digests, no claim of bit-for-bit reproducibility.

Keep the existing npm package-lock and npm ci. Worker lock includes the
15 exact direct/transitive Python distributions observed in successful PR
#26 integration (job 139063903327) and PR #27 build (job 139077658047).
The general requirements files are unchanged, avoiding changes to existing
production task dependency policy. Docker and isolated CI use the lock;
pip check and runtime inventory equality verify no unexpected distributions.
The lock is version-pinned, not wheel-hash-pinned. Wheel hashes must be
independently resolved/verified before claiming hash reproducibility.

Remove unused npm/yarn/corepack tools from final Web filesystem and pip,
setuptools, wheel, ensurepip from Worker final runtime. Do not remove system
libraries by hand. Underlying base layers may still contain build/installer
files; final image and embedded-secret scan remain mandatory. Test imports,
UID/GID, no package manager, locked dependencies, real PostgreSQL 17 and
18.7 Alpine, local archive, authentication, test isolation, container
recreation, and normal Node/Python regression tests.

Trivy produces CycloneDX SBOMs and separate blocking secret scans and
HIGH/CRITICAL vulnerability scans for each tested image. Unfixed findings
are included; no ignore or waiver mechanism added. Each gate runs even
when other gates fail if the corresponding image was built. SBOM artifacts
are inventory, not a passing security verdict. Findings may remain;
if so, inspect fixed versions and runtime reachability, propose further
remediation or explicitly approved narrowly scoped risk acceptance;
never lower the current gate without separate approval.

After security acceptance, pin the actual reviewed base digests in source,
rerun all checks, then separately revise the publication workflow to use
the new reviewed commit. No images may be published using PR #27's old
source pin. No merge or deployment in this phase.

## Phase 11E-2 — shell-free Web and patched glibc Worker candidate

Prior 11E-1 actual CI (run 45577928914): Web 51 HIGH/0 CRITICAL,
Worker 54 HIGH/0 CRITICAL; both database matrices and all runtime tests
passed; remaining findings were Debian system packages. No exceptions
were accepted.

Web now uses official gcr.io/distroless/nodejs22-debian13:nonroot for
the final runtime, with Node 22 Trixie for build. CI resolves three
base manifests once, not just two. Distroless includes Node, TLS roots
and required libraries, without a shell/package manager. Numeric user
10001:10010 and cache/standalone paths remain unchanged. The test overlay
starts a JS launcher directly, awaits the same database/credential
guard, then starts Next.js in the same process. Healthcheck is exec-form
with the absolute Node binary. Compose smoke still tests wrong database
rejection, fixture login, SQL/archive, read-only archive, recreation
and readiness. No weakened assertion or bypass.

Worker retains Python 3.11 on official glibc Trixie slim because
Distroless Debian 13 Python is Python 3.13, incompatible with the
reviewed 3.11 wheel lock without a separate ABI/dependency migration.
The Worker shared base stage performs signed APT upgrades with no
additional recommended packages. Installer tools remain stripped,
dpkg metadata and system libraries remain intact. CI records installed
Debian package versions and tests TLS trust store, SQLite, zlib, native
DuckDB, NumPy, Pandas, psycopg, BaoStock and timezone support.
No package database deletion or vulnerability exclusion is used.
This is an incremental minimal-runtime candidate, not a custom scratch
Python build or a fully immutable package snapshot; APT versions can
change across builds. Final digest and exact package provenance must
be reviewed after scanning. Wheel hash lock remains outstanding.

Only build and test on PR #28; no GHCR push, merge, VPS or production
modification. Retain all HIGH/CRITICAL findings including unfixed.
