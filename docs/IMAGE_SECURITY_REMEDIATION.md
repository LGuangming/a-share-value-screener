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

## Phase 11E-3 — Node 26 LTS and Python 3.13 Distroless

11E-2 actual run 45578701994: Web 3 HIGH/0 CRITICAL, Worker 53 HIGH/0
CRITICAL; application/Compose smoke, identity, dependency, native
runtime and secret checks passed. Strict vulnerability gate blocked.

Candidate builds now use Node 26 and Python 3.13 on Debian 13; final
images use official nodejs26-debian13:nonroot and python3-debian13:nonroot.
Resolver obtains four actual immutable registry references. Availability,
architecture and runtime compatibility must be proven in CI, not assumed.
Python wheels are installed with --require-hashes from a single
CPython 3.13 linux/amd64 hash lock generated from actual downloaded wheels
and shared across both PostgreSQL matrices. Exact dependency versions
are retained initially; missing wheel or new dependency fails resolution
rather than falling back to source builds or unreviewed updates.
The candidate hash lock is a CI artifact until reviewed and committed;
a later commit must pin all four verified base digests and the reviewed
hash lock, then rerun checks. Local build currently requires that
generated lock file; do not release from floating defaults.
The lock records downloaded bytes, not an independent signature audit.
Node package-lock.json remains unchanged. Unit CI retains Python 3.11
baseline and adds Python 3.13, while integration/Node builds use upgraded
versions. Production workflow dependency files remain untouched.

Worker copies only installed wheel site-packages into native Debian
Python 3.13 Distroless, not a venv with incompatible interpreter paths.
No shell, package manager, manual shared-library deletion or fake
package metadata. Direct Python test launcher runs original database
guard and execs pipeline with unchanged arguments. Fixtures use the
absolute interpreter path; subprocess uses sys.executable.
Runtime and SQL/archive tests must prove native wheel/ABI, TLS,
timezone, compression, SQLite, PostgreSQL and readiness compatibility.
SBOM replaces dpkg-query inventory for shell-free image. Zero HIGH and
CRITICAL, including unfixed, and secret scan remain mandatory.
No registry push, merge, VPS deployment or production changes.

## Phase 11E-4 — runtime diagnosis, verified zstd update and patch comparison

11E-3 run 45579331976 built both images and passed all application
tests; Worker failed the installer module assertion before imports
and Compose smoke. Actual culprit not printed by that run. The
new build-only script logs each module origin before removing only
optional installer code from allowed paths, fails on ambiguous paths,
and keeps all Debian and Python package metadata. Runtime checks
still require no installers, exact distributions and native imports.
No scanner exceptions. Debian venv metadata can still be flagged.

Both final images receive the full libzstd1 1.5.7+dfsg-1+deb13u1
amd64 .deb from signed Debian APT sources. The extracted payload and
original control/md5sums are installed in Distroless's documented
status.d format, not a fabricated version string. Verify payload
checksums in both actual final images and native loader in Worker.
APT download fails closed if fixed version is unavailable. Other
system packages and metadata remain intact. Package version is
pinned; repository access and package bytes still need actual CI
verification, and archive SHA256 is printed at build time.

Four actual prior manifest digests and 15 prior CP313 wheel hashes
are now committed as candidate locks, not release approval. CI
verifies manifest identity and independently downloads and hashes
the exact wheels, compares the committed lock, and builds with
--require-hashes. Application integration also uses the hash lock.
Source default digests match the committed lock. No auto-refresh.

Separate slim-evaluation stage/job tests the official CPython 3.13
slim build image with APT upgrades and removed Python installers,
compares actual patch version, native imports, size and strict
Trivy findings; shell remains for this comparison. It is not the
default target, does not run the complete Compose matrix and is
not approved for deployment. Distroless remains the primary
candidate. All full application and two primary DB matrices and
strict secret/HIGH/CRITICAL scans retained. No publish or merge.

## Phase 11E-5 — residual CVE triage and minimal CPython evaluation

See [REMAINING_CVE_REVIEW.md](REMAINING_CVE_REVIEW.md). The 37 HIGH findings
represent 19 CVEs; all remain open in Debian Trixie at review time.
Preserve current primary image and its full CI. Pin the previously
observed zstd .deb SHA256 before extraction. Add isolated CPython 3.13.18
Distroless cc evaluation with signed full native library packages,
actual provenance, SBOM, strict scans and native smoke tests. No merge,
registry push, VPS, production modification or waiver.

## Phase 11E-6 — Minimal candidate native closure and full Compose matrix

11E-5 run 45581428494 failed at _tkinter: libtk8.6 and libtcl8.6 were absent in the official Slim builder. The candidate now constructs a headless stdlib omitting only explicit GUI modules (Tkinter, _tkinter, IDLE, turtle and turtledemo) and the existing installer exclusions; it scans only retained ELF files plus all wheel native files. CI checks direct application AST imports and records wheel GUI import references (including optional/test paths); this is not proof of dynamic non-reachability. No global missing-library exception. The actual candidate base digest is pinned separately in deploy/minimal-base.lock, leaving the four main image pins unchanged. Debian packages are selected from installed builder versions, checked against the signed APT index SHA256, and extracted with their original status.d metadata. Final image checks validate package file checksums, runtime-base metadata, zstd, actual dynamic loader closure and exact wheel inventory. The two independent PostgreSQL candidate jobs use the same Web image and unchanged smoke suite via a CI-only overlay and a constrained worker variant. Primary Distroless and Slim jobs remain intact; all scans still block HIGH/CRITICAL including unfixed, and secret findings. Candidate provenance, runtime verification and SBOM are artifacts, not release approval. Upstream CPython scanner coverage still requires independent CVE assessment; package archive hashes outside zstd are observed per build, not permanently locked. No publish, merge or deployment.
