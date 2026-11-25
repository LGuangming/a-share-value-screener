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
