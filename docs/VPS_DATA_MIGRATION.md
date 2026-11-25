# VPS data migration — one offline full backup and restore

**Current plan, supersedes D01-B-5D shared/independent instance proposals.**
Use the existing ashare-postgres and its existing ashare DB. No additional
container, database, role, HBA rule, remote dispatch command or incremental
rehearsal is needed. Historical independent-mode tools/tests remain for
regression evidence only; do not run their provisioning scripts. Shared-mode
development is discontinued and its changes are removed from the active branch.

## Approval gates

1. Before any paid window: prepare the local tools, confirm existing target
   owner and application roles, confirm the ashare target really has no user
   objects or client sessions, verify existing container/digest and local TCP
   access. If any gate fails, stop. A pre-created schema with zero rows is NOT
   an empty database for this plan; no automatic DROP/TRUNCATE/CLEAN.
2. Separately approve Launch and the final read-only export. Freeze all Neon
   database and object writers (scheduled and manual jobs; wait for running
   jobs), and keep VPS target writers stopped. Confirm billing terms and
   preserve the old read service. No production connection under the current
   development-only approval.
3. Use the existing postgres.py backup (standard pg_dump -Fc with exported
   repeatable-read snapshot), and storage.py sync for all keys in the approved
   private bucket. Compare initial/final inventory, downloaded bytes, SHA256
   and producer manifests; no source write/delete. Keep all objects, including
   legacy prefixes, in the backup. No incremental loop or previous generation.
4. Review source.json and source.toc locally. direct_restore.py restore verifies
   the exact existing container, target owner, bound TCP session, DB OID and
   cluster ID against preflight, checks empty target and capacity, captures a
   target-before.dump and ACL evidence, and uses pg_restore --no-owner --no-acl
   --single-transaction --exit-on-error (never --create or --clean). It then
   checks every supported schema definition and deterministic table row hash
   against the dump snapshot. It applies database-local grants only to the
   existing screener_reader and screener_writer, verifies ACLs, and writes a
   receipt. No role creation or role changes. Source scope is the eight public
   tables in database/neon-schema.sql; unexpected tables, executable objects,
   extensions or schemas require review instead of silently excluding them.
5. Archive activation, application configuration, smoke tests and cutover
   require separate approval. Keep source backups and source services intact.
   Downgrade only after reconciliation, stable operation and billing review.

## Administrator commands (future approved execution only)

Use a root terminal, never paste credentials into chat, shell command arguments
or Git. The private working directory must be fresh, owned by root and on a
filesystem with sufficient space. Obtain this reviewed revision of
scripts/migration and its tests/production-policy.json locally before use.
Check pg_dump/pg_restore major >= source (18 recommended), python3-venv and
TLS CA availability before Launch. Install missing packages only after approval.

```bash
sudo -i
set -euo pipefail
umask 077
# Set this to the reviewed local checkout; do not use an unreviewed branch.
TOOLS=/root/a-share-value-screener/scripts/migration
test -f "$TOOLS/direct_restore.py"
test -f "$TOOLS/tests/production-policy.json"
pg_dump --version
pg_restore --version
WORK=$(mktemp -d /root/ashare-full.XXXXXX)
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install 'psycopg[binary]>=3.2,<4' 'boto3>=1.40,<2' 'pyarrow>=19,<25' 'duckdb>=1.2,<2'
PY="$WORK/venv/bin/python"
CID=$(docker --host unix:///var/run/docker.sock inspect --type container --format '{{.Id}}' ashare-postgres)
echo "WORK=$WORK"
```

Use a root-only pgpass file with exact entries (no wildcards). This helper
prompts without echoing passwords and escapes colon/backslash; use it for both
source and target. Use actual existing target DB owner, not the app writer.

```bash
mkpass() {
  "$PY" - "$@" <<'PYCODE'
import getpass, os, sys
path, host, port, db, user = sys.argv[1:]
def esc(v): return v.replace(chr(92),chr(92)*2).replace(":",chr(92)+":")
pw = getpass.getpass("Database password: ")
if not pw or "\n" in pw or "\r" in pw: raise SystemExit("invalid password")
with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600),"w") as f:
    f.write(":".join(map(esc,(host,port,db,user,pw)))+"\n")
PYCODE
}
read -rp 'Existing VPS ashare owner: ' OWNER
mkpass "$WORK/target.pgpass" 127.0.0.1 5432 ashare "$OWNER"
target() {
  env -i PATH="$PATH" HOME=/root LC_ALL=C PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=ashare PGUSER="$OWNER" PGPASSFILE="$WORK/target.pgpass" PGSSLMODE=disable "$PY" "$TOOLS/direct_restore.py" "$@" --root "$WORK" --container-id "$CID"
}
# Only AFTER separately approved local target read:
target check --approved-target-read
```

Only AFTER source export approval, confirmed writer freeze and approved Launch:

```bash
read -rp 'Neon direct hostname (not pooler): ' NH
read -rp 'Neon database name: ' ND
read -rp 'Neon source login: ' NU
[[ "$NH" == *.neon.tech && "$NH" != *-pooler* ]]
mkpass "$WORK/source.pgpass" "$NH" 5432 "$ND" "$NU"
env -i PATH="$PATH" HOME=/root LC_ALL=C PGHOST="$NH" PGPORT=5432 PGDATABASE="$ND" PGUSER="$NU" PGSSLMODE=verify-full PGSSLROOTCERT=/etc/ssl/certs/ca-certificates.crt PGPASSFILE="$WORK/source.pgpass" "$PY" "$TOOLS/postgres.py" backup --root "$WORK" --approved-live-source
sha256sum "$WORK/source.dump"
# Read-only S3 credentials: confirm bucket is private and exact endpoint in Neon Console.
(
  read -rp 'Neon S3 HTTPS endpoint: ' NEON_STORAGE_ENDPOINT
  read -rp 'Neon S3 region: ' NEON_STORAGE_REGION
  read -rsp 'Neon S3 access key: ' NEON_STORAGE_ACCESS_KEY_ID; echo
  read -rsp 'Neon S3 secret key: ' NEON_STORAGE_SECRET_ACCESS_KEY; echo
  export NEON_STORAGE_ENDPOINT NEON_STORAGE_REGION NEON_STORAGE_ACCESS_KEY_ID NEON_STORAGE_SECRET_ACCESS_KEY
  "$PY" "$TOOLS/storage.py" sync --root "$WORK/objects" --bucket a-share-backtest-private --workers 2 --approved-live-read
)
"$PY" "$TOOLS/storage.py" verify --root "$WORK/objects"
"$PY" "$TOOLS/archive_verify.py" --root "$WORK/objects" --memory-limit 256MB
# Review the private source.json/source.toc, verify-storage.json, verify-archive.json.
# Only AFTER explicit final restore approval, still with writers stopped:
target restore --approved-target-read --approved-final-restore
target verify --approved-target-read
```

Do not run these blocks now. No password or connection string needs to be
committed. Existing target roles must already meet the app ACL requirements.
The local TCP port may be unreachable with an internal Docker network; if
check fails, stop and review; do not change the firewall or networking.
The CI tests use a fixture network namespace, not evidence of real host access.

## Archive activation and rollback

The verified download tree is WORK/objects/files/a-share-backtest-private/
and retains every object key. Runtime raw prefix is
a-share-backtest/01_raw_data/. After separate activation approval, copy the
verified raw prefix to a fresh same-filesystem staging path under
/opt/ashare/archive, verify copied hashes, ownership and app mount contract,
then rename to the final path only when it is absent. If the destination
already exists, do not overwrite or merge; review it first. Keep the full
bucket backup, including legacy keys, independent of runtime files. PR #28
application local archive support must be accepted before cutover.

Restore failure rolls back the pg_restore transaction. A later comparison or
ACL failure can leave a successfully restored database; leave it offline,
retain both dumps and all evidence, and do not automatically retry, delete,
truncate or drop. Reverting to the old application/source configuration is
the first rollback after cutover (before new writes). After VPS writes begin,
reconcile them before rollback; do not silently discard them. Never use
--clean/--create against the existing container. Any destructive target
reset needs separate explicit approval and verified backup.

The existing historical 100MB/500MB/1GB and 6000-object tests support sizing,
not actual source capacity. Final capacity gate is based on real source size,
including WAL and backup headroom. Source changes after the snapshot or S3
download invalidate the migration: keep writers frozen until cutover or
abort, reopen old writers and take a new full backup later. No promise about
Neon billing or actual transfer duration.

### Reviewed existing network topology

The direct target guard accepts only the existing internal bridge `ashare-db` alone or together with the reviewed non-internal bridge `1panel-network`, with the latter attached only to the approved PostgreSQL container. Network IDs must match the container attachments. Unknown networks, non-internal `ashare-db`, or additional management-network peers block the migration. Keep both existing VPS networks; no network changes are required. The host port remains loopback-only. The management bridge means the entire container is not network-isolated. Recheck network scope before actual restore.
