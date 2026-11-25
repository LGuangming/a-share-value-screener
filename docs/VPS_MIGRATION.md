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
