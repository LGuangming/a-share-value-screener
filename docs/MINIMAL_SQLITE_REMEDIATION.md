# 11E-9 — candidate SQLite remediation

The 11E-8 CI run 45587475126 proved five HIGH findings in Minimal, two
in Web, zero CRITICAL, Expat 2.11.0 independent gate passed, 158 ELF graphs.
All 36 Worker application Python source files were reviewed for direct
sqlite/sqlite3/_sqlite3/read_sql/to_sql/sqlite_scan/sqlite_attach references:
none found. The PostgreSQL store and DuckDB archive engine remain unchanged.
The build now audits installed hash-verified wheels and all application
Python ASTs, recording direct imports and short literal references (not a
proof of absence of all dynamic/native use). Retain Python SQLite rather
than risk breaking Pandas or other optional/indirect consumers.

Use the official 3.54.1 autoconf archive (SHA256 cross-check Buildroot
and APSW, SHA3-256 official downloads, sqlite3.c SHA3-256 and exact source
ID official release log). SQLite 3.54.0 is the reviewed minimum for the
three remaining SQLite CVEs. Compile an upstream shared library with
same libsqlite3.so.0 SONAME, hardened flags and observed feature flags
from original runtime. No distro library overwrite, fake dpkg package,
or metadata suppression. Debian builder package stays intact; do not
copy it to candidate. Record old/new compile options, source identity,
actual library digest, and actual wheel/source references. Reject
unsupported special parser features rather than silently omit them.
Run matching CPython test_sqlite3 against the new library; final image
verifies actual loader path, SQLite source ID, hash, Pandas SQL roundtrip,
FTS5, JSON, integrity, exact packages and all retained ELF graphs.
Keep old hash in historical exclusions; seven remaining Debian archive
locks remain unchanged. Generate explicit upstream SQLite component
CycloneDX separately from existing scanner-generated SBOMs. Independent
gate requires source identity, reviewed version and provenance; it is
not an exhaustive upstream vulnerability database or severity waiver.
Do not infer absence of all vulnerabilities from disappearance of distro
package; SQLite CLI-specific issue CVE-2026-119586 also reviewed in Debian
tracker, no shell built here. No exploit tests.

Existing primary runner, Slim, both database matrices, Expat audit, old
and new strict Trivy and secret gates remain in force. No SQLite CLI,
compiler or build files copied. Build toolchain not yet fully pinned.
New candidate expected to report four OpenSSL HIGH findings across Web
and Worker if scanners accept the repaired component; actual scan pending.
No changes to OpenSSL, no production changes.

Sources: https://www.sqlite.org/releaselog/3_54_1.html
https://sqlite.org/download.html?topic=1057
https://lists.buildroot.org/pipermail/buildroot/2026-October/813774.html
https://github.com/rogerbinns/apsw/blob/master/checksums
https://security-tracker.debian.org/tracker/source-package/sqlite3
