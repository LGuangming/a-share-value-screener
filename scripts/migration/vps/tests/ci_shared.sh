#!/usr/bin/env bash
# Disposable GitHub-hosted runner only. Never run on the actual VPS.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]] || exit 64
unset DOCKER_HOST DOCKER_CONTEXT PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR PGPASSWORD ASHARE_MIGRATION_MODE
export DOCKER_HOST=unix:///var/run/docker.sock
root=/opt/ashare/migration
image=postgres@sha256:0115267ab5a509022df9319c77be365cef335adb71556c75f60f5360ee247e71
[[ ! -e /opt/ashare && ! -L /opt/ashare ]]
! getent passwd ashare-migrate >/dev/null
[[ "$(stat -c %u /)" == 0 && ! -L /opt && -d /opt ]]
sudo chown root:root /opt
sudo chmod go-w /opt
sudo install -d -m 0755 /opt/ashare
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin ashare-migrate
sudo /usr/bin/python3 scripts/migration/vps/prepare.py --mode shared --approved-local-prepare
[[ ! -e "$root/compose.yml" && ! -e "$root/bin/control.py" && ! -e "$root/config/private/db-secrets/admin" ]]
sudo -u ashare-migrate test -r "$root/config/client/owner.pgpass"
sudo -u ashare-migrate test ! -w "$root/bin"
sudo -u nobody test ! -r "$root/config/client/owner.pgpass"
docker pull --platform linux/amd64 "$image"
docker network create --internal ashare-db >/dev/null
docker run -d --name ashare-postgres --network ashare-db \
  --cpus=1.5 --memory=800m --memory-swap=800m --shm-size=256m \
  -p 127.0.0.1:5432:5432 \
  --health-cmd 'pg_isready -h 127.0.0.1 -U postgres -d postgres' \
  --health-interval 2s --health-retries 30 \
  -e POSTGRES_PASSWORD=fixture-only-password \
  -e POSTGRES_INITDB_ARGS='--auth-local=peer --auth-host=scram-sha-256' \
  "$image" -c shared_buffers=192MB -c work_mem=4MB -c max_connections=30 >/dev/null
cid=$(docker inspect --format '{{.Id}}' ashare-postgres)
ready=0
for i in $(seq 1 60); do
  if [[ "$(docker inspect --format '{{.State.Health.Status}}' "$cid")" == healthy ]]; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]]
admin(){ docker exec -i --user postgres "$cid" psql -X -w -qAt -v ON_ERROR_STOP=1 -U postgres -d "$1"; }
printf '%s\n' "CREATE ROLE screener_reader LOGIN;" "CREATE ROLE screener_writer LOGIN;" "CREATE DATABASE ashare;" "CREATE DATABASE ashare_test;" "CREATE DATABASE ashare_source;" | admin postgres
for db in ashare ashare_test; do
  printf '%s\n' "CREATE TABLE public.sentinel (id int PRIMARY KEY, value text NOT NULL);" "INSERT INTO public.sentinel VALUES(1,'$db');" "GRANT SELECT ON public.sentinel TO screener_reader;" | admin "$db"
done
admin ashare_source < scripts/migration/tests/fixture.sql
# Fingerprint every protected fixture schema/data/ACL and the existing roles,
# memberships, DB owner, ACL and connection flags. No migration role is filtered
# out of protected database ACLs: any new grant would change the fingerprint.
fingerprint(){
  for db in ashare ashare_test; do
    docker exec --user postgres "$cid" pg_dump -U postgres -d "$db" --restrict-key=FixtureStableKey | sha256sum
  done
  printf '%s\n' "SELECT datname,oid,datdba,datacl,datallowconn,datconnlimit FROM pg_database WHERE datname IN ('ashare','ashare_test','postgres','template1','template0','ashare_source') ORDER BY datname;" \
    "SELECT oid,rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit,rolconnlimit FROM pg_roles WHERE rolname NOT LIKE 'ashare_mig_%' ORDER BY rolname;" \
    "SELECT * FROM pg_auth_members ORDER BY oid;" | admin postgres | sha256sum
  docker inspect --format '{{.Id}}|{{.State.StartedAt}}|{{.RestartCount}}|{{.Image}}|{{json .HostConfig.PortBindings}}|{{json .NetworkSettings.Networks}}' "$cid"
}
before=$(fingerprint)
run_admin(){ sudo /usr/bin/python3 "$root/bin/shared_admin.py" "$@" --container-id "$cid"; }
if run_admin init --approved-shared-init; then exit 1; fi
[[ "$(echo "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ashare_mig_%';" | admin postgres)" == 0 ]]
echo 'PASS: missing scoped HBA denies initialization before role changes'
# The only HBA modification is to a disposable fixture container.
# This is NOT an instruction to edit a real VPS automatically.
sudo /usr/bin/python3 "$root/bin/shared_admin.py" hba-plan |
docker exec -i --user postgres "$cid" sh -ec 'hba=$(psql -X -w -At -U postgres -d postgres -c "SHOW hba_file"); tmp=$(mktemp "$hba.fixture.XXXXXX"); cat >"$tmp"; cat "$hba" >>"$tmp"; mv "$tmp" "$hba"; psql -X -w -At -U postgres -d postgres -c "SELECT pg_reload_conf();"'
sleep 2
echo "CREATE ROLE ashare_mig_reader NOLOGIN;" | admin postgres
if run_admin init --approved-shared-init; then exit 1; fi
[[ "$(echo "SELECT count(*) FROM pg_database WHERE datname='ashare_migration';" | admin postgres)" == 0 ]]
echo "DROP ROLE ashare_mig_reader;" | admin postgres
echo 'PASS: role collision blocked without database creation'
if run_admin init; then exit 1; fi
run_admin init --approved-shared-init
[[ "$(fingerprint)" == "$before" ]]
echo 'PASS: shared init leaves both protected databases and existing roles unchanged'
# Internal Docker networks on some engines do not publish to host loopback.
# Use only the fixture container network namespace to exercise real TCP 5432
# with host filesystem and restricted OS account; do not change network policy.
pid=$(docker inspect --format '{{.State.Pid}}' "$cid")
py=$(command -v python)
export PYTHONPATH=scripts/migration
printf '127.0.0.1:5432:ashare_source:postgres:fixture-only-password\n' | sudo tee "$root/config/private/source.pgpass" >/dev/null
sudo chmod 600 "$root/config/private/source.pgpass"
# The fixture role uses TCP SCRAM. Source dump is synthetic only.
sudo nsenter -t "$pid" -n -- env -i PATH="$PATH" PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=ashare_source PGUSER=postgres PGSSLMODE=disable PGPASSFILE="$root/config/private/source.pgpass" "$py" scripts/migration/postgres.py backup --root "$root/postgres/fixture"
sudo chown -R ashare-migrate:ashare-migrate "$root/postgres/fixture"
target(){
  (cd "$root/bin" && sudo nsenter -t "$pid" -n -- runuser -u ashare-migrate -- env -i PATH="$PATH" PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=ashare_migration PGUSER=ashare_mig_owner PGSSLMODE=disable PGPASSFILE="$root/config/client/owner.pgpass" ASHARE_MIGRATION_MODE=shared "$py" "$@")
}
target "$root/bin/postgres.py" restore --root "$root/postgres/fixture" --approved-isolated-restore
target "$root/bin/postgres.py" verify --root "$root/postgres/fixture"
target "$root/bin/permissions.py" apply --root "$root/postgres/fixture" --policy "$root/bin/fixture-acl-policy.json" --approved-isolated-acl
if target "$root/bin/postgres.py" restore --root "$root/postgres/fixture" --approved-isolated-restore; then exit 1; fi
echo 'PASS: shared owner restore, logical verification and ACL; non-empty restore rejected'
# Real authenticated target role SQL (TCP inside the fixture).
role_psql(){
  local key="$1" role="$2" db="$3" query="$4"
  sudo nsenter -t "$pid" -n -- env -i PATH="$PATH" PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE="$db" PGUSER="$role" PGSSLMODE=disable PGPASSFILE="$root/config/private/db-secrets/$key.pgpass" psql -X -w -At -v ON_ERROR_STOP=1 -c "$query"
}
[[ "$(role_psql reader ashare_mig_reader ashare_migration 'SELECT count(*) FROM stocks;')" == 2 ]]
if role_psql reader ashare_mig_reader ashare_migration "INSERT INTO stocks(code) VALUES('denied');"; then exit 1; fi
role_psql writer ashare_mig_writer ashare_migration "INSERT INTO stocks(code) VALUES('fixture-write');"
for db in ashare ashare_test postgres template1; do
  for role in ashare_mig_owner ashare_mig_reader ashare_mig_writer; do
    err=$(docker exec --user postgres "$cid" psql -X -w -h 127.0.0.1 -U "$role" -d "$db" -c 'SELECT 1' 2>&1) && exit 1
    [[ "$err" == *'pg_hba.conf rejects connection'* ]]
  done
done
echo 'PASS: real reader/writer access and cross-database HBA rejections'
run_admin monitor
if run_admin pause; then exit 1; fi
run_admin pause --approved-shared-pause
if role_psql reader ashare_mig_reader ashare_migration 'SELECT 1'; then exit 1; fi
[[ "$(echo "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ashare_mig_%' AND NOT rolcanlogin;" | admin postgres)" == 3 ]]
[[ "$(fingerprint)" == "$before" ]]
[[ "$(echo "SELECT count(*) FROM pg_database WHERE datname='ashare_migration';" | admin postgres)" == 1 ]]
[[ "$(echo "SELECT count(*) FROM stocks;" | admin ashare_migration)" == 3 ]]
echo 'PASS: shared pause retains database, data, container and protected database fingerprints'
