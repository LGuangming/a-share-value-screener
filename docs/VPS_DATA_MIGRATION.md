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
