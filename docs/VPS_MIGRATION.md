# VPS migration — phase 05: database reader compatibility

This phase changes the website reader only. No production migration, schema
changes, deploy, data archive changes or writer configuration changes.

## Runtime configuration

| Environment | DATABASE_DRIVER | DATABASE_READ_URL |
| --- | --- | --- |
| Existing Vercel | unset or neon | existing restricted Neon URL |
| Self-hosted VPS | postgres | standard TCP PostgreSQL URL for a restricted reader |

The reader never falls back to DATABASE_URL. Keep credentials server-side.
Restart the Next.js process when changing the mode or URL. Use TLS with
certificate verification for remote PostgreSQL; for a private Docker network
or loopback, no public port is needed. Do not expose TCP 5432 to the Internet.
Do not grant the website INSERT, UPDATE, DELETE, DDL or administrator rights.
The TCP adapter uses a small pool (4 connections per process), read-only
transactions by default, 5-second connection and 15-second statement limits.
A database role with SELECT-only grants remains the primary permission boundary.

All existing tagged SQL is unchanged; interpolation uses positional parameters.
DATE values are returned as calendar strings so timezone changes do not shift
trade/report dates. JSONB, numeric, timestamp and bigint use pg's normal decoding
and existing application normalization.

## Verification

Run npm install, npm run test:db, npm run typecheck, npm run build and the
existing test suite. The CI integration step provisions a disposable PostgreSQL
17 service and a restricted test reader; the source production major version is
not yet known. It uses synthetic fixtures only. The integration test is skipped
when TEST_POSTGRES_URL is unset and refuses non-loopback/non-fixture targets.
Never point it at a production database. A real Neon comparison and VPS smoke
test remain required after source access is restored.

No change to archive storage, worker scheduling, Docker deployment or DNS is
included in phase 05. Do not merge or deploy before explicit review.

## Phase 06 — read-only archive compatibility

The website defaults to ARCHIVE_STORAGE_DRIVER=neon and preserves the private
branch-scoped HTTPS S3 endpoint and restricted reader credentials. Set
ARCHIVE_STORAGE_DRIVER=local and ARCHIVE_LOCAL_ROOT=/data/archive to read the
same object keys from the VPS. Bind mount /opt/ashare/archive:/data/archive:ro.
Retain a-share-backtest/01_raw_data and every manifest, content-addressed
snapshot, original Parquet and JSONL.GZ object; do not rewrite manifests.
The configured root must exist, be absolute and canonical, and not be a
symlink or the filesystem root. The archive writer must be trusted, with
immutable parent directories and atomic publication; the web mount is read-only.
Unknown driver values fail closed. There is no fallback to Neon.
Missing files return null, malformed/corrupt/oversized files fail validation.
Prefix listings support the two existing list patterns; maxKeys overflow is
reported as truncated and rejected by the existing business readers (no silent
partial listing). No general-purpose pagination API is exposed in this phase.
Cache keys distinguish storage mode and root/bucket identity; existing TTLs
are retained. No additional service or public static file route is introduced.

Tests: npm run test:archive. For real Parquet tests install Python duckdb and
set ARCHIVE_TEST_FIXTURES=1 (CI does so). Synthetic fixtures test annual and
incremental archives, recent 120 sessions, factors, financial and ST manifests,
corruption, and parity with a mocked S3 transport. No actual Neon requests.
Python archive writers, inventory/export and production data verification are
outside this phase. The actual VPS mount and full production archive must be
verified before cutover. No merge or deployment without approval.

## Phase 07 — Python local pipeline

Python already uses psycopg TCP; continue to use a restricted ingestion
writer DATABASE_URL, not the website reader. Install
backend/requirements-worker.txt; historical BaoStock jobs may require the
existing optional BaoStock dependency. Set ARCHIVE_STORAGE_DRIVER=local,
ARCHIVE_LOCAL_ROOT=/data/archive. Default cloud driver and existing Actions
remain unchanged. Worker mount is writable; website mount is read-only.
Use a shared group for the reader and writer (files 0640, directories 0750).
Use a dedicated canonical filesystem root owned by trusted operators.

All existing active archive commands use the StorageConfig/s3_client factory.
Local metadata is stored under .archive_meta/objects, keyed by canonical object
key and content SHA-256, outside the public data namespace. A file without a
matching metadata record fails closed; do not synthesize trusted metadata by
hashing unverified downloads. During the later transfer phase use verified
source checksums/length/content type, preserve original modification time and
source ETag in the migration inventory, and ingest the exact bytes through the
local adapter. Local ETag identifies a file version, not the source ETag.
Staging files are on the same filesystem and fsynced before atomic rename.
Metadata is written first, so old data remains readable if publication fails.
Old metadata entries may remain after interruption; no automatic sidecar
garbage collection. This is not a transactional filesystem or a security
boundary against privileged writers; real crash recovery and disk limits need
VPS validation. Immutable binary objects cannot be implicitly replaced.

Run scheduled/manual VPS tasks through the same single-host lock:
```
PYTHONPATH=backend python -m value_screener.local_pipeline market
PYTHONPATH=backend python -m value_screener.local_pipeline financial --budget 40
PYTHONPATH=backend python -m value_screener.local_pipeline maintenance
PYTHONPATH=backend python -m value_screener.local_pipeline backtest --year 2025
```
Local run UUID recorded in job metrics, never a fabricated GitHub run ID.
A failed upstream command stops downstream steps; skipped/unchanged upstream
jobs do not publish a new NCAV result. No cron/scheduler is installed. All
mutating manual archive commands must be serialized with this pipeline by
operators; do not run raw modules concurrently or across multiple hosts.
DuckDB uses 2 threads and 512MB managed-memory limit in local mode; optional
DUCKDB_TEMP_DIRECTORY is an absolute scratch path. This is not an RSS limit.
The engineering backtest restores verified files into temporary disk before
running; it is not an investment performance result or PIT certification.

Maintenance defaults to audit only. Local apply requires explicit environment
opt-in and pipeline invocation; preserve all existing retention, backup, date,
archive and website gates. The local website health origin is restricted to
the internal web service or loopback port 3000, no external redirect, optional
private session cookie. Missing/expired auth blocks cleanup; do not enable
apply until actual authenticated local health checks are verified. Existing
retired consolidation and legacy deletion entry points reject local mode.
The writer adapter only permits healthcheck deletion or explicitly enabled
obsolete monthly-reader deletion; original daily data cannot be deleted by it.
No production cleanup, provider calls or schedule changes in this PR.

CI uses temporary directories, isolated PostgreSQL 17 and synthetic provider
data. It verifies market ingestion, real Parquet daily/month publication,
archive-first financial import and NCAV calculation, repeatability, metadata
and atomic failure tests. Production archive migration, actual data coverage,
multi-strategy performance, throughput and real VPS authenticated maintenance
remain separate acceptance steps.
