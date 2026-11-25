# Phase 11E-5 — residual CVE review (2026-11-25)

Baseline: run 45580255773, jobs 139088356470 and 139088356549.
Web 2 HIGH, Worker 35 HIGH, zero CRITICAL; 19 distinct CVEs.
All 19 remain listed as vulnerable in Debian Trixie on 2026-11-25.
No waiver, no severity override, no publication. Counts are package-CVE
findings, not independent defects. See each Debian tracker URL:
https://security-tracker.debian.org/tracker/CVE-<ID>.

| CVE | Findings Web/Worker | Upstream/repair state | Preliminary reachability |
|---|---|---|---|
| CVE-2026-107494 | 1/1 | OpenSSL code fix announced, 3.5.10 scheduled Dec 1; Trixie unfixed | QUIC peer path; loopback HTTP/TCP and ordinary HTTPS/PG clients do not establish QUIC usage |
| CVE-2026-124709 | 1/1 | Same release schedule; Trixie unfixed | QUIC server stream handling; no application QUIC listener demonstrated |
| CVE-2026-15308 | 0/4 | Debian Python vulnerable; upstream CPython changelog/advisory review required for exact patch | HTMLParser input; no direct use established; transitive parsing unknown |
| CVE-2026-19445 | 0/4 | CPython 3.13.16 release documents repair | Server-side SSL SNI callback; Worker is a client, no TLS server demonstrated |
| CVE-2026-19553 | 0/4 | CPython 3.13.16 release documents repair | TLS wrap_bio hostname validation; inspect dependency paths; do not waive |
| CVE-2026-7210 | 0/4 | Debian Python vulnerable; upstream Python/Expat fixes need build-specific verification | XML parse of untrusted input; boto3/botocore can process XML responses in S3 driver |
| CVE-2026-82049 | 0/4 | CPython 3.13 changelog documents repair | tar extraction with crafted links; direct business use not demonstrated |
| CVE-2026-113351 | 0/1 | Expat fixed in newer Debian suites, Trixie vulnerable | XML input, possible SDK path |
| CVE-2026-115506 | 0/1 | Same | XML input, possible SDK path |
| CVE-2026-117610 | 0/1 | Same | XML input, possible SDK path |
| CVE-2026-118370 | 0/1 | Same | XML input, possible SDK path |
| CVE-2026-119373 | 0/1 | SQLite 3.54.0 fix; Trixie vulnerable | FTS5-specific, no direct production SQL use shown |
| CVE-2026-119374 | 0/1 | Same | FTS5-specific, no direct production SQL use shown |
| CVE-2026-119379 | 0/1 | Same | Session extension, no direct production use shown |
| CVE-2026-76642 | 0/1 | util-linux newer suites fixed, Trixie vulnerable | mount helper, not libuuid API itself |
| CVE-2026-78408 | 0/1 | Same | nsenter command path, no such app use demonstrated |
| CVE-2026-78409 | 0/1 | Same | mount subdirectory operation, not libuuid API itself |
| CVE-2026-78410 | 0/1 | Same | restricted bind mount operation, not libuuid API itself |
| CVE-2025-69720 | 0/2 | ncurses newer suites fixed, Trixie vulnerable | infocmp command path, no direct app use demonstrated |

Sources (official): https://security-tracker.debian.org/tracker/source-package/openssl
https://security-tracker.debian.org/tracker/source-package/python3.13
https://security-tracker.debian.org/tracker/source-package/expat
https://security-tracker.debian.org/tracker/source-package/sqlite3
https://security-tracker.debian.org/tracker/source-package/util-linux
https://security-tracker.debian.org/tracker/source-package/ncurses
https://openssl-library.org/news/secadv/20261110.txt
https://openssl-library.org/news/secadv/20261117.txt
https://www.python.org/downloads/release/python-31316/
https://docs.python.org/3.13/whatsnew/changelog.html

Source inspection at 1b45be63dc65335de44f246cff22301b340c7e13:
compose.yaml drops ALL capabilities, sets no-new-privileges and numeric
UIDs; compose.loopback.yaml binds loopback; compose.vps-test.yaml restricts
worker egress network and uses guarded test DB. local_pipeline.py executes
fixed Python modules via sys.executable and uses uuid4; store.py uses
psycopg; tushare_client.py uses urllib HTTP(S); neon_archive.py uses boto3
with S3 backend; history_parquet.py uses DuckDB. These are bounded static
observations, not proof of non-exploitability. Test fixtures are not full
production paths; dependency calls, loaded symbols, runtime egress and
adversarial data flow have not been exhaustively verified. CI saves
source-usage.txt for review. No exploit tests or scanner exceptions.
The OpenSSL upstream risk rating differs from Trivy HIGH in some cases;
retain Trivy severity. Recheck released 3.5.10 and signed Debian updates
after Dec 1; do not build speculative unreleased packages into primary.

The zstd .deb SHA256 observed identically in four prior builds is now
checked before extracting payload. Minimal CPython candidate copies
upstream interpreter/stdlib and actual hash-verified wheels onto
Distroless cc Debian 13, collecting native Debian libraries as full
signed-repository packages with original status.d metadata. It does not
copy the older Debian Python packages or falsify metadata. The upstream
interpreter must be independently assessed against release notes and
actual library versions; Trivy may not recognize non-dpkg CPython.
Candidate uses observed 3.13.18 builder digest and resolves its separate
cc runtime digest during evaluation; candidate digest not permanently
approved. Native tests and strict secret/vulnerability scans are
required; full Compose matrix remains required for any future adoption.
No claim of compatibility/security until logs are reviewed.
