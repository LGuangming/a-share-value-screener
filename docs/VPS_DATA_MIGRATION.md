# VPS data migration — D01-B preparation only

This branch is isolated from the application PR stack. No production credentials are
referenced by its CI; no live migration, merge, deployment, upgrade or cutover is authorized.

## Before Launch
1. Review the fixture CI matrix (PostgreSQL 17 and 18 sources to PostgreSQL 18 target,
   simulated S3 with two buckets). The floating fixture image tags are not production images.
2. Verify the real VPS locally with scripts/migration/inventory_vps.sh; record permissions,
   filesystem/inodes, PostgreSQL versions, databases and actual sizes using a dedicated
   local read-only account. Do not grant Docker socket or shell access to ashare-ops.
3. Design a root-owned dispatcher with fixed no-argument commands data-inventory and
   data-capacity, validated output limited to aggregate metrics. No arbitrary SQL,
   shell, upload, forwarding, docker group, sudo wildcards or credentials in logs.
   Existing ping/status remain unchanged. Implementation on VPS requires separate approval.
4. Provision a private isolated ashare_migration database and role with ownership only
   of that database, no superuser/CREATEDB/CREATEROLE, no production database access.
   Restrict connections to loopback. Source account needs CONNECT, schema USAGE,
   SELECT on all included tables/sequences; RLS must not hide rows (review owner/BYPASSRLS
   requirements without granting broad rights by default).
5. Prepare source direct endpoint with TLS verify-full, private PGPASSFILE (0600),
   separate read-only S3 credentials (list all expected buckets and read objects).
   Independently confirm bucket scope in Neon Console. If ListBuckets isn't supported,
   stop and design an approved console/API-backed scope mechanism.
6. Use separate private evidence directories (0700) on VPS, not the application archive;
   run only one process per generation. Files are per bucket to prevent collisions.
   Ensure sufficient disk for backup, restore, WAL, temp sorts, archive and rollback.
   Never copy untrusted dumps to a privileged server or restore as superuser in production.
7. Complete application security and deployment acceptance, real permissions test,
   Parquet schema/rows, manifest references and DuckDB business validation. These
   are explicit gates not yet implemented as dataset-specific validators in this PR.
8. Record current billing, maximum compute, autosuspend, quota reset and downgrade
   constraints. Approve the exact paid window separately; no assumption that downgrading
   while above Free quota is possible.

## Approved future paid-window commands (not executable by CI)
Use explicit PGHOST, PGPORT, PGUSER, PGDATABASE, PGSSLMODE=verify-full and a private
PGPASSFILE. Do not use DATABASE_URL on command lines or echo credentials.
Run from scripts/migration with a PostgreSQL client major >= source and compatible
with target. Direct Neon endpoint only. Example command forms, no real credentials:

    python postgres.py inventory --root /private/evidence --approved-live-source
    python postgres.py backup --root /private/generation --approved-live-source

Prefer backup over separate inventory: backup captures inventory and dump under the
same exported REPEATABLE READ snapshot, so the separate inventory can be skipped
to avoid source scans. Row count and sorted COPY digest each scan the data; for
large tables evaluate cost and disk spill. Sequences are not MVCC; require a final
approved writer freeze or reconciliation. No claim of cross-system atomicity.
Logical comparison is deliberately strict; cross-version catalog/deparser/custom
types may need explicit reviewed normalization. Foreign tables fail closed.
Large objects, custom types/operators, aggregate functions, rules, grants/default
privileges, tablespaces, publication/subscription and role membership require a
separate catalog/TOC review. Restore deliberately strips ownership and ACL; rebuild
least privilege and verify reader/write denial plus writer permitted operations before cutover.

Switch environment to isolated loopback database ashare_migration, never ashare:

    python postgres.py restore --root /private/generation --approved-isolated-restore
    python postgres.py verify --root /private/generation

Restore requires a new empty database. No automatic drop/clean/overwrite.
A failed restore is not evidence of migration success. On mismatch inspect private report,
not public CI logs. Source dump + JSON are a pair; keep authenticated off-host backups
and a signed external hash receipt before production switch.

Storage commands (credentials only from dedicated environment):

    python storage.py inventory --root /private/archive-generation --bucket APPROVED_BUCKET --approved-live-read
    python storage.py sync --root /private/archive-generation --bucket APPROVED_BUCKET --approved-live-read
    python storage.py verify --root /private/archive-generation

Repeat --bucket for all independently confirmed buckets. Full listing (no prefix restriction).
Read-only GET with If-Match, length and available metadata SHA256; SHA256 computed from
stream, ETag is not SHA256. Repeat skips only locally rehashed verified files. Object-level
resume, not byte-range resume. SDK retries requests; failed streamed transfers retry on
next invocation. Changed/deleted objects require a fresh generation and reconciliation,
not overwrite/delete. Empty directory-marker keys fail for manual mapping review.
Archive promotion must explicitly map each approved bucket's relative keys to the final
archive root, retaining all other buckets separately. Final full listing and DB snapshot
must use a common approved writer freeze/manifest watermark. Archive file hashes alone
do not prove Parquet schema, row coverage or manifest correctness.

## Readiness
readiness.py accepts a local readiness.json with exactly the named gates in REQUIRED.
Each entry is {"approved":true,"reviewer":"...","reviewed_at":"...","sha256":"..."}
with corresponding <gate>.json containing passed:true and underlying reviewed evidence.
It checks file integrity and completeness only, not authenticity or technical correctness.
Never fabricate evidence or use fixture success as live readiness. Separate approval for
Launch, final freeze, cutover, downgrade and deletion. Retain original source until
rollback window/backup recovery accepted. No cleanup in this PR.


## D01-B-2 strengthened local evidence
- source.toc is retained and hash-bound to the dump. Additional enum/domain, rules,
  ACL/default ACL, role membership and special-object inventory. Large objects,
  foreign servers, event triggers, publications, custom aggregates/operators stop
  automatic migration; full custom type/collation/tablespace/subscription review
  remains a manual TOC gate. Not a claim of exhaustive PostgreSQL catalog support.
- permissions.py applies an explicit table/sequence allowlist only to loopback
  ashare_migration. Roles must already exist and meet restricted attributes.
  Example policy is fixture-only, not production. Actual schema/roles reviewed
  before creating production policy. Rebuild ACL after every isolated restore.
  Checks effective table, column, schema, sequence, database and function access;
  no arbitrary role creation, no grants on other databases. For production, test
  real separate login credentials, RLS, role inheritance, database CONNECT outside
  the isolated DB, and security-definer/trigger behavior independently.
- storage.py sync --previous-generation /private/old-generation reuses only
  unchanged objects by verified independent copy; changed objects are fetched into
  a new generation; removed objects retained in old generation. Never pass old
  generation as target. Object-level interruption retry, not byte-range resume.
- archive_verify.py --root /private/archive-generation reads every Parquet batch,
  compares DuckDB row counts, reports schema and date ranges, checks supported
  manifest object_key/partition_files hash, size, fields and rows. Unknown manifest
  forms and unreferenced Parquet fail closed; real catalog/pointer shapes, JSON.GZ
  financial payload validation and dataset-specific completeness need explicit
  review; historical revisions may intentionally overlap. Do not delete them.
- Fixture CI records /usr/bin/time -v and evidence bytes; these are runner-side
  measurements, not estimates of Neon billed compute or VPS throughput. CI recreates
  only the disposable local target and restores from an independent backup copy.


## D01-B-3 pressure and archive-format preparation
CI only: actual eight-table schema with 120k/600k/1.2m synthetic quote rows plus
compact history, financials and screening rows; measured database bytes determine
actual tier. Both containers use postgres:18.7-alpine, 1.5 CPU, 800 MiB memory,
192 MiB shared_buffers, 4 MiB work_mem, 30 connections and 60s idle timeout.
Snapshot export uses a transaction-local override, bounded pg_dump subprocess.
Serial single-transaction restore remains the only production default. Parallel
jobs=2 benchmark is non-atomic and is used only after disposable fixture reset.
Small tier repeats the final full snapshot after a source change; this is not
logical replication or incremental PostgreSQL replication.

Disk-backed S3-shaped load fixture: 6,000 synthetic keys and measured bytes,
100 large Parquet files and 2,900 small ST-shaped gzip files plus manifests,
controlled 1ms request overhead; compare 1/2/4 workers, 256/512MB DuckDB, new
generation reuse. Worker scope capped at 2 CPU and 1 GiB. No remote HTTP
throughput claim: Moto tests separately exercise S3 protocol client semantics.
The checkpoint journal writes a durable receipt per object, then publishes
atomically; interrupted transfers are retried per object. Torn journal or
unverified file fails closed. No byte-level resume or concurrent processes.

Local archive verifier checks recognized catalog receipts, nested references,
gzip JSONL/JSON arrays, financial fields and frozen-universe coverage, disclosure
periods, hashes, counts and known ST batch code lists. Unknown manifest forms,
retired catalog references and unsupported binary formats remain review gates;
actual production manifests not retrieved. The historical catalog is a receipt
not a full current inventory. No claim of point-in-time financial certification.
Production-schema candidate ACL uses all eight tables for read/write, DELETE
only for screen_results and retention tables. Application DDL/bootstrap and
reader grant compatibility must be assessed with the final worker build.
No actual Neon/VPS permissions or resources changed.
