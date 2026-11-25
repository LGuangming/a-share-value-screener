# Phase 11E-14 — post-M12 application integration (not deployment approval)

M12 data migration is complete and PR #29 was closed without merge
(final cleanup commit 2c8d578d4870a13923b7bcfa3156195621a89ce6). Prior
operator acceptance recorded eight public tables, 624,037 rows, 12 indexes,
186/186 ACL checks, 5,911 archive objects and matching metadata entries,
578,064,096 bytes. Acceptance was verified_with_retention_exceptions
(two previous reader versions retained; date coverage is not SQL/archive
row-level parity). Application started and production cutover were false.
These are historical evidence, not a live query or independent re-verification
by this PR. Retain backup and rollback records; no new migration, no source
restore, no reindex, no archive rewrites, no deletion or provider calls.

## Existing production contract (future separately approved launch only)

* Existing 1Panel-managed ashare-postgres / ashare, internal ashare-db;
  PostgreSQL is not managed by application Compose. Confirm current container
  digest, internal network, postgres alias and loopback-only host binding
  from metadata locally; no production DB connection in 11E-14.
* Base compose.yaml plus compose.loopback.yaml only for production; never
  compose.test.yaml, compose.vps-test.yaml, compose.minimal-ci.yaml or
  compose.distroless-ci.yaml. Exact approved immutable Web/Minimal image
  pair required. No release digests currently approved.
* Web: DATABASE_DRIVER=postgres, DATABASE_READ_URL uses screener_reader
  at postgres:5432/ashare; no writer credentials, no DDL, read-only SQL
  transactions. Worker: DATABASE_URL uses screener_writer; never postgres
  owner. Both use /data/archive backed by /opt/ashare/archive, Web read-only
  UID:GID 10001:10010, Worker read-write 10002:10010. Set ARCHIVE_DIR
  explicitly to the existing canonical absolute path, no staging rename or
  recursive chown. Confirm actual file and directory ownership/permissions,
  sidecar and lock ownership, trusted parents and no symlinks before launch.
  Data files use 0640, shared directories 2750; Worker-only metadata may be
  more restrictive. No secret-bearing config or raw env output.
* Web cache and Worker scratch must be separate from archive and DB.
  Keep maintenance apply false; Worker profile is one-shot, no schedule.
  Web must be verified before any separately approved Worker write or NCAV
  recalculation. Old cloud writers must remain frozen; confirm actual
  workflow state before enabling local writers. Do not delete old cloud data.

## ACL and runtime DDL review

M12 historical policy (PR #29 source at 24a14fb) allowed SELECT on all eight
tables for reader/writer, INSERT/UPDATE on all eight for writer, DELETE only
on daily_prices, price_history, price_history_fetches, screen_results.
No writer CREATE/TEMP/TRUNCATE/REFERENCES/TRIGGER or role management; no
sequences needed. Actual role grants are not re-queried in this phase.

Web uses a restricted pool with default_transaction_read_only=on and does
not bootstrap schema. Worker Store previously issued CREATE TABLE IF NOT
EXISTS plus GRANT on every history check, including NCAV recalculation.
Local mode now checks the existing two tables/columns in a read-only
transaction, failing on absent/incompatible tables without attempting DDL.
Local init-db rejects before opening a connection. Cloud bootstrap remains
unchanged; bootstrap-prices validates history tables before stock upserts.
Ordinary upsert/job run/financial import require SELECT/INSERT/UPDATE;
screen publication requires scoped DELETE on screen_results; optional
retention requires DELETE on the three retention tables and remains disabled.
No broader grant is needed by the reviewed statements. CI fixture ACL
matches the M12 table privilege allowlist and removes PUBLIC schema and
database access; the full local market/archive/financial/NCAV integration
previously ran as restricted writer on PG17 and PG18.7; current CI targets
only PG18.7. Docker smoke checks the
restricted writer and existing schema in both variants. These are fixture
checks, not live ACL acceptance or a full production schema fingerprint.
Actual compatibility remains pending CI and separately authorized live
read-only catalog/role checks; no privilege change on production.

## Archive and isolation review

Existing key layout a-share-backtest/01_raw_data/ and reader.manifest.json
pointers are retained; Python local adapter verifies SHA-256 against
.archive_meta/objects/<key-hash-prefix>/<key-hash>/<content-hash>.json.
Web reads files via its read-only mount and checks reader manifest content
where implemented; do not claim universal Web sidecar hash enforcement.
No regeneration or overwriting of verified sidecars. Future read-only host
inspection must check actual directory/lock permissions; M12 evidence does
not independently prove every numeric owner matches the image identity.
Retain previous reader versions, keep maintenance audit-only. CI generates
synthetic files via the same writer and exercises reader access.
No real archive file was accessed in this phase.

Isolated tests continue to use ashare_test, /opt/ashare/test/archive,
guarded entrypoints, internal Worker egress, maintenance apply disabled.
Use a dedicated fixture-only DB network, not production ashare-db; test.env
sample now names ashare-test-db. Never repoint test guards to ashare or
mount production data under the test path. No new fixture cluster is
provisioned here. If no isolated fixture DB is available on VPS, leave this
gate pending rather than use the migrated DB. CI remains disposable.

## Next gates

1. Verify the exact reviewed PR SHA: PostgreSQL 18.7 restricted ACL regressions and
   default Minimal Compose smoke. 11E-14A supersedes the OpenSSL remediation
   block only for the two approved exact Debian package findings until
   2026-12-26 00:00 UTC; see [scoped risk acceptance](VPS_CANDIDATE_RISK_ACCEPTANCE_11E14A.md).
   All other HIGH/CRITICAL, secrets, runtime checks and candidate acceptance remain mandatory.
2. Obtain current read-only VPS host inventory (no SQL): actual resources,
   existing container/network, mount and directory metadata, no public DB
   or Web binding, free ports and file ownership. Old 30 GiB migration
   reserve and absent migration-root tests no longer apply. Use measured
   remaining disk/RAM with existing database/archive and backup retention,
   including Docker storage and temporary image/scratch headroom. Previous
   VPS size assumptions are not live measurements.
3. Separately approve live read-only checks using existing restricted
   credentials (no owner or write operations), update PR #27 only after
   separate authorization and final reviewed security-passed SHA.
4. Separately authorize private GHCR publication, digest-pair validation,
   isolated VPS test, production Web read-only startup, and only then
   Worker writes/schedule/cutover. Keep original data and backups; stop
   only application containers for rollback, never delete DB/volumes/archive.

No changes to PR #29, PR #27, main, production data, deployment, release or
security scanning. This is not a release certificate.

Current CI acceptance is Python 3.13 and PostgreSQL 18.7 only. Retain all
business, ACL, archive, isolation and candidate security checks. Historical
cloud scheduling workflows remain unchanged until separate cutover approval.
