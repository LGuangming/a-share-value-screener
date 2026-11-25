#!/usr/bin/env bash
# CI only: existing synthetic ashare target, no live endpoints.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]] || exit 64
unset DOCKER_HOST DOCKER_CONTEXT PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR PGPASSWORD ASHARE_MIGRATION_MODE
export DOCKER_HOST=unix:///var/run/docker.sock
image=postgres@sha256:0115267ab5a509022df9319c77be365cef335adb71556c75f60f5360ee247e71
docker network create --internal ashare-db >/dev/null
docker run -d --name ashare-postgres --network ashare-db --cpus=1.5 --memory=800m --memory-swap=800m \
  -p 127.0.0.1:5432:5432 --health-cmd 'pg_isready -h 127.0.0.1 -U postgres -d postgres' \
  --health-interval 2s --health-retries 30 -e POSTGRES_PASSWORD=fixture-only-password \
  -e POSTGRES_INITDB_ARGS='--auth-local=peer --auth-host=scram-sha-256' "$image" >/dev/null
cid=$(docker inspect --format '{{.Id}}' ashare-postgres)
for i in $(seq 1 60); do
  [[ "$(docker inspect --format '{{.State.Health.Status}}' "$cid")" == healthy ]] && break
  sleep 1
done
[[ "$(docker inspect --format '{{.State.Health.Status}}' "$cid")" == healthy ]]
admin(){ docker exec -i --user postgres "$cid" psql -X -w -qAt -v ON_ERROR_STOP=1 -U postgres -d "$1"; }
printf '%s\n' "CREATE ROLE ashare_owner LOGIN PASSWORD 'fixture-only-password';" \
 "CREATE ROLE screener_reader LOGIN CONNECTION LIMIT 8 PASSWORD 'fixture-only-password';" \
 "CREATE ROLE screener_writer LOGIN CONNECTION LIMIT 8 PASSWORD 'fixture-only-password';" \
 "CREATE DATABASE ashare OWNER ashare_owner;" "CREATE DATABASE ashare_test;" "CREATE DATABASE ashare_source;" | admin postgres
printf '%s\n' "CREATE TABLE public.sentinel(id int PRIMARY KEY);" "INSERT INTO public.sentinel VALUES(42);" | admin ashare_test
admin ashare_source < database/neon-schema.sql
printf '%s\n' "INSERT INTO stocks(ts_code,name) VALUES('000001.SZ','fixture');" | admin ashare_source
fingerprint(){ docker exec --user postgres "$cid" pg_dump -U postgres -d ashare_test --restrict-key=FixtureStableKey | sha256sum; }
before=$(fingerprint)
root=$(sudo mktemp -d /root/ashare-direct.XXXXXX)
printf '%s\n' '127.0.0.1:5432:ashare_source:postgres:fixture-only-password' '127.0.0.1:5432:ashare:ashare_owner:fixture-only-password' | sudo tee "$root/pgpass" >/dev/null
sudo chmod 600 "$root/pgpass"
pid=$(docker inspect --format '{{.State.Pid}}' "$cid")
py=$(command -v python)
# The fixture uses its own network namespace, not a production Docker network.
run(){ local db=$1 user=$2; shift 2; sudo nsenter -t "$pid" -n -- env -i PATH="$PATH" PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE="$db" PGUSER="$user" PGSSLMODE=disable PGPASSFILE="$root/pgpass" "$py" "$@"; }
direct(){ run ashare ashare_owner scripts/migration/direct_restore.py "$@" --root "$root" --container-id "$cid"; }
if direct check; then exit 1; fi
if run ashare_test ashare_owner scripts/migration/direct_restore.py check --root "$root" --container-id "$cid" --approved-target-read; then exit 1; fi
echo 'PASS: unapproved and wrong database blocked'
direct check --approved-target-read
run ashare_source postgres scripts/migration/postgres.py backup --root "$root"
if direct restore --approved-target-read; then exit 1; fi
direct restore --approved-target-read --approved-final-restore
direct verify --approved-target-read
[[ "$(echo 'SELECT count(*) FROM stocks;' | admin ashare)" == 1 ]]
if direct restore --approved-target-read --approved-final-restore; then exit 1; fi
[[ "$(fingerprint)" == "$before" ]]
[[ "$(echo "SELECT count(*) FROM pg_database WHERE datname='ashare_migration';" | admin postgres)" == 0 ]]
[[ "$(echo "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ashare_mig_%';" | admin postgres)" == 0 ]]
echo 'PASS: standard direct restore, full row hashes, existing ACLs, no repeat restore or new database/roles; ashare_test unchanged'
