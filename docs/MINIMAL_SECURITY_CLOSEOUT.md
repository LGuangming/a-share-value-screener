# 11E-7 — Minimal security closeout / 2026-11-25

Scope: PR #28 only. Default runner unchanged. No waiver or publication.
11E-6 run 45584825846, jobs 139103906688 and 139103906767:
162 retained ELF graphs, 15 wheel distributions, 12 copied Debian packages
and both isolated Compose tests passed. Minimal 11 HIGH / 0 CRITICAL;
primary worker 35 HIGH / 0 CRITICAL; Web 2 HIGH / 0 CRITICAL.
Trivy 0.70.0 did not independently enumerate embedded Expat.

## Upstream CPython and Expat
CPython 3.13.18 release changelog includes the fixes for the previously
reviewed Python issues (CVE-2026-15308, 19445, 19553, 7210, 82049).
The exact interpreter source-image digest remains pinned; the new
provenance records and rechecks executable, libpython and pyexpat bytes.
The changelog names bundled Expat 2.8.5, whereas the observed image
reported 2.9.0. Do not infer exact downstream patch contents from a
version alone. Capture actual final pyexpat linkage and version.
Expat 2.10.0 (2026-11-02) fixes CVE-2026-113351, 115506, 117610, 118370.
2.11.0 (2026-11-23) fixes further issues (CVE-2026-124317, 128241,
134703, including xmlwf-specific issue). Require at least that reviewed
version floor or a separately verified repair; current 2.9.0 does not
meet it. Independent gate deliberately fails closed, after Compose, and
retains report even on failure. This finite review is not exhaustive and
does not claim all issues affect the same runtime path or have HIGH
severity. No CVE exploit payloads. An actual pinned upstream Expat rebuild
(or reviewed later official CPython image) is a subsequent engineering
decision; not silently performed here.

## Remaining 11 scanner findings
| Family | Findings in Minimal | Evaluation |
|---|---:|---|
| SQLite | 3 | 3.46.1-7+deb13u2 in Trixie; upstream 3.54.0 fixes, newer Debian suite 3.54.1-2. Retain compatible library pending tested signed backport or reviewed source rebuild. |
| ncurses | 2 | CVE-2025-69720 relates to infocmp command, not proof the linked library is safe. 6.5+20250216-2 in Trixie. Evaluate removal of _curses/_curses_panel and readline only after actual ELF consumer review and separate full regression; do not remove now. |
| libuuid | 4 | util-linux source findings relate to mount/nsenter. Newer suites fixed, Trixie still affected; capture ELF consumers and presence of utilities, retain library and scanner status. |
| OpenSSL | 2 | 3.5.9-1~deb13u1, QUIC issues. Upstream fix release planned Dec 1 per 11E-5 evidence; recheck signed Debian packages and released upstream artifacts, do not assume availability. |

Web adds the same two OpenSSL findings (13 findings combined).
No mixed-suite installation, source-package renaming, fabricated dpkg
records, ignore file, VEX or severity downgrade. Upstream custom SQLite
even with an actual repair may remain reported by Trivy Debian advisory
data when no Trixie fixed version is present. Preserve strict gate.
Runtime ELF consumer evidence supports an engineering decision, not
proof of exploitability or its absence.

## Permanent candidate package lock
deploy/minimal-debian-amd64.lock.json records all 12 archive hashes from
two identical actual CI jobs. Enforce exact set, installed builder version,
signed APT index SHA256 and committed SHA256 before extraction. If the
repo no longer serves the archive, fail closed; no automatic update.
Five base image digests, 15 wheels, zstd archive remain pinned.

## Scanner comparison
Keep existing 0.70.0 primary and Minimal gates intact. On each successful
candidate build, run pinned action with explicit Trivy v0.76.2 for Minimal
and the same Web image, separate CycloneDX, secret JSON and strict
HIGH/CRITICAL JSON (ignore-unfixed false, exit 1). Always run after prior
failures, preserve reports. No substitution for the independent upstream
component review or primary full CI. Original SBOM plus newer SBOM plus
component report cover distinct evidence; no fabricated inventory.
CI results pending.

Sources:
- https://docs.python.org/3.13/whatsnew/changelog.html
- https://github.com/libexpat/libexpat/blob/master/expat/Changes
- https://github.com/libexpat/libexpat/releases/tag/R_2_11_0
- https://security-tracker.debian.org/tracker/source-package/expat
- https://security-tracker.debian.org/tracker/source-package/sqlite3
- https://security-tracker.debian.org/tracker/source-package/ncurses
- https://security-tracker.debian.org/tracker/source-package/util-linux
- https://security-tracker.debian.org/tracker/source-package/openssl
- https://github.com/aquasecurity/trivy/discussions/11603
