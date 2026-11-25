# 11E-10 — 2026-11-25

11E-9A run 45589738213 both Minimal matrices: SQLite 3.54.1,
159 ELF graphs, 15 wheels, 7 Debian runtime archives, 2 HIGH worker
and 2 HIGH Web, zero CRITICAL. Actual library hash matches both builds.
OpenSSL advisories Nov 17 and 24 confirm source fixes and Dec 1 planned
3.5.10 release. Debian tracker both CVE-2026-107494 and CVE-2026-124709:
Trixie 3.5.9-1~deb13u1 and security 3.5.7-1~deb13u3 vulnerable.
Do not downgrade, mix distributions, substitute an unreleased tree or waive
HIGH findings. Record actual Node runtime and system libssl metadata
separately; Node may bundle different OpenSSL. Worker records loaded
shared library linkage and metadata. No claim of exploitability or absence.
Next review official release, signed Debian package/hash and both runtime
ABIs; retain strict scans and all regression tests.

SQLite ALLOW_ROWID_IN_VIEW is an upstream legacy behavior option; SQLite
recommends leaving it disabled. Keep disabled in candidate; compare actual
old/new implicit view ROWID behavior, assert explicit ROWID alias works
and new implicit ROWID is rejected. This is a documented compatibility
decision, not a claim all old behavior is preserved. MAX_SCHEMA=10000000
is new default; remaining 59 reported options equal in 11E-9A.
36 application files had no direct SQLite reference; 33 wheel references
all pandas; not exhaustive dynamic reachability.

11E-9A had XML 564 run/14 skipped and SQLite 514 run/4 skipped in each matrix.
Prior summary logs do not contain individual skip reasons. Use verbose
upstream regrtest to record actual names and reasons without enabling
extra external resources or suppressing failures; review actual CI output.

38 newly installed toolchain archives observed identical by name,
architecture/version in both matrices (not the seven runtime archives).
Commit exact version set, download each exact archive from signed APT
indices and verify hash/identity, install local archives with no downloads
and assert exact package delta. Existing pinned image holds base packages.
Record proposed SHA256 lock and require review before committing hashes;
this commit does not claim all 38 archive hashes are permanently locked.
No self-mutating workflow. Compiler remains build-only.
Supplement scanner SBOMs with explicit CPython, Expat and SQLite
components and their provenance; keep build-only toolchain separate.
Independent inventory is not an exhaustive scanner or a vulnerability waiver.

Sources:
https://openssl-library.org/news/secadv/20261117.txt
https://openssl-library.org/news/secadv/20261124.txt
https://security-tracker.debian.org/tracker/CVE-2026-107494
https://security-tracker.debian.org/tracker/CVE-2026-124709
https://sqlite.org/capi3ref.html

## 11E-10A — permanent archive hashes and offline install

Both 11E-10 jobs 139126119164 (PG17) and 139126119045 (PG18.7), run
45591294216, independently downloaded all 38 build-only archives and
verified each byte hash against the signed APT index. Exact name, arch,
version and SHA256 tuples match in both raw logs. Commit all 38 SHA256s
with source run/job IDs; make every hash mandatory. Prior install failed
with APT --no-download on local filenames, after successful verification.
Separate network-enabled download/verification from a Docker RUN
--network=none install layer. Rehash all local archives and verify dpkg
identity and the complete base inventory again; unpack only those files,
configure with dpkg, verify exact installed delta and no broken packages
(dpkg audit and apt-get check). No dependency repair/download or force flags.
Export verified hash inventory instead of proposed candidate hash inventory.
Retain existing ROWID tests, verbose upstream regression, supplemental
SBOM and actual OpenSSL reporting, both complete database matrices and
strict vulnerability gates. OpenSSL remains blocked pending official fix.
No merge, publish, deployment or production changes.
