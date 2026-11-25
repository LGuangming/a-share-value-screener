# 11E-8 — directed component remediation (2026-11-25)

11E-7 actual report (run 45586307579 job 139108891341) showed 11
HIGH Minimal, 2 HIGH Web in both scanner versions, and seven independent
Expat version blockers. CPython source v3.13.18 _elementtree checks
the exact major/minor/micro version of the pyexpat CAPI. Rebuilding
pyexpat alone is incorrect. Rebuild both extensions with the matching
CPython release source, official Expat 2.11.0 static archive, and original
pinned interpreter ABI. Verify SHA256 against python.org release and
Expat release asset digest; retain source locks, build toolchain inventory,
old/new extension hashes, XML regression test evidence and license notices.
The rest of interpreter and stdlib remain copied from the existing pinned
image. No package metadata is erased or fabricated. Source archive hashes
are permanent; build toolchain packages are fetched from signed Debian
repositories and recorded but not yet permanently pinned. If toolchain
install changes any required runtime package version, the existing lock
check fails. This is candidate-only; no new interpreter release assumed.

Exclude optional curses/readline and _uuid extensions alongside the prior
GUI exclusions; retain uuid.py and verify its pure-Python fallback,
uuid1/uuid4/uuid5, round-trip and actual full application Compose smoke.
AST audit rejects direct application imports of removed modules and
records wheel references, not proof of all dynamic calls. No blanket
missing-library suppression. Four archive entries removed from the
active closure (libncursesw6, libreadline8t64, libtinfo6, libuuid1);
retain prior hashes in historical exclusions. All eight remaining
packages must match exact closure, version, signed APT index and committed
SHA256. Absence from final metadata and dynamic loading checks required.
No package files installed and then metadata hidden. Expected 6 fewer
scanner findings, not yet observed. Original default and Slim comparison
remain untouched.

SQLite: upstream 3.54.1 released 2026-10-16; sqlite3.c SHA3-256
e2b869d3003b33c942b7dd7063604fbc53553e1b66609570df39b9b7b558ee81
(official changes.html). Trixie remains 3.46.1-7+deb13u2, CVEs still open;
retain _sqlite3 and libsqlite3; no cross-suite mixing or scanner metadata
workaround. A future verified upstream build must preserve required
compile flags, C ABI, actual source inventory and independent scan coverage.
OpenSSL official November 17/24 advisories schedule 3.5.10 for Dec 1;
Trixie still 3.5.9-1~deb13u1. Retain libssl and libcrypto, no speculative
release or waiver. Keep Web unchanged. If exclusions and XML rebuild pass,
expected residual candidate scan 7 HIGH (3 SQLite, 4 OpenSSL across images),
not a prediction of actual scanning or release approval.

Keep both database matrices, application CI, old and new Trivy gates and
independent upstream component security check. No publish, merge, deploy.
Sources: https://www.python.org/downloads/release/python-31318/
https://github.com/libexpat/libexpat/releases/tag/R_2_11_0
https://github.com/python/cpython/blob/v3.13.18/Modules/_elementtree.c
https://sqlite.org/changes.html
https://security-tracker.debian.org/tracker/source-package/sqlite3
https://security-tracker.debian.org/tracker/source-package/openssl
https://openssl-library.org/news/secadv/20261117.txt
https://openssl-library.org/news/secadv/20261124.txt
