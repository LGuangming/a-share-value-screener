#!/usr/bin/env bash
# CI only: existing synthetic ashare target, no live endpoints.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]] || exit 64
unset DOCKER_HOST DOCKER_CONTEXT PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR PGPASSWORD ASHARE_MIGRATION_MODE
export DOCKER_HOST=unix:///var/run/docker.sock
image=postgres@sha256:0115267ab5a509022df9319c77be365cef335adb71556c75f60f5360ee247e71
root=$(sudo mktemp -d /root/ashare-direct.XXXXXX)
docker pull --platform linux/amd64 "$image"
docker network create --internal ashare-db >/dev/null
docker run -d --pull never --platform linux/amd64 --mount "type=bind,src=$root,dst=$root" --name ashare-postgres --network ashare-db --cpus=1.5 --memory=800m --memory-swap=800m \
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
# Mount only the disposable, root-private evidence directory at the same path.
# Execute the exact pinned server image's clients through fixed-ID wrappers.
sudo install -d -m 0700 "$root/client-bin"
for tool in pg_dump pg_restore psql; do
  {
    printf '%s\n' '#!/bin/bash' 'set -euo pipefail'
    printf 'cid=%q\nroot=%q\ntool=%q\n' "$cid" "$root" "$tool"
    cat <<'CLIENT'
[[ "$PGHOST" == 127.0.0.1 && "$PGPORT" == 5432 && "$PGPASSFILE" == "$root/pgpass" ]]
case "$PGDATABASE:$PGUSER" in
  ashare_source:postgres|ashare:ashare_owner) ;;
  *) exit 64 ;;
esac
exec /usr/bin/docker --host unix:///var/run/docker.sock exec --user 0 \
  --env PGHOST --env PGPORT --env PGDATABASE --env PGUSER --env PGPASSFILE \
  --env PGSSLMODE=disable --env PGCONNECT_TIMEOUT=10 --env LC_ALL=C \
  "$cid" "/usr/local/bin/$tool" "$@"
CLIENT
  } | sudo tee "$root/client-bin/$tool" >/dev/null
  sudo chmod 0700 "$root/client-bin/$tool"
  version=$(docker exec --user 0 "$cid" "/usr/local/bin/$tool" --version)
  [[ "$version" == *"18.7"* ]]
  printf 'PINNED_CLIENT %s: %s\n' "$tool" "$version"
done
echo 'PASS: PostgreSQL clients from fixed-image container; no APT client installation'
printf '%s\n' '127.0.0.1:5432:ashare_source:postgres:fixture-only-password' '127.0.0.1:5432:ashare:ashare_owner:fixture-only-password' | sudo tee "$root/pgpass" >/dev/null
sudo chmod 600 "$root/pgpass"
pid=$(docker inspect --format '{{.State.Pid}}' "$cid")
py=$(command -v python)
# The fixture uses its own network namespace, not a production Docker network.
run(){ local db=$1 user=$2; shift 2; sudo nsenter -t "$pid" -n -- env -i PATH="$root/client-bin:$PATH" PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE="$db" PGUSER="$user" PGSSLMODE=disable PGPASSFILE="$root/pgpass" "$py" "$@"; }
direct(){ run ashare ashare_owner scripts/migration/direct_restore.py "$@" --root "$root" --container-id "$cid"; }
expect_block(){
  local code=$1 output; shift
  if output=$("$@" 2>&1); then echo "unexpected success: $code"; exit 1; fi
  printf '%s\n' "$output"
  grep -Fxq "FAILED: $code" <<<"$output"
}
expect_block APPROVAL_REQUIRED direct check
expect_block TARGET_ENDPOINT run ashare_test ashare_owner scripts/migration/direct_restore.py check --root "$root" --container-id "$cid" --approved-target-read
echo 'PASS: unapproved and wrong database blocked'
# A real nonempty fixture must still be refused, even with zero table rows.
echo 'CREATE TABLE public.must_be_empty(id int);' | admin ashare
# Owned by postgres; ashare_owner must not require SELECT to reject it.
expect_block TARGET_NOT_EMPTY direct check --approved-target-read
echo 'DROP TABLE public.must_be_empty;' | admin ashare
direct check --approved-target-read
echo 'PASS: original single internal network accepted'
# Disposable runner only: exercise the exact reviewed VPS network topology.
docker network create --driver bridge 1panel-network >/dev/null
docker network connect 1panel-network "$cid"
direct check --approved-target-read
echo 'PASS: reviewed 1panel-network plus internal ashare-db accepted'
docker network create --driver bridge unexpected-network >/dev/null
docker network connect unexpected-network "$cid"
expect_block NETWORK_MEMBERSHIP direct check --approved-target-read
docker network disconnect unexpected-network "$cid"
docker network rm unexpected-network >/dev/null
echo 'PASS: unknown third network rejected; original two networks retained'
direct check --approved-target-read
run ashare_source postgres scripts/migration/postgres.py backup --root "$root"
expect_block APPROVAL_REQUIRED direct restore --approved-target-read
direct restore --approved-target-read --approved-final-restore
direct verify --approved-target-read
[[ "$(echo 'SELECT count(*) FROM stocks;' | admin ashare)" == 1 ]]
role_sql(){
  docker exec --user 0 --env PGPASSWORD=fixture-only-password "$cid" psql -X -w -At -h 127.0.0.1 -p 5432 -U "$1" -d ashare -v ON_ERROR_STOP=1 -c "$2"
}
[[ "$(role_sql screener_reader 'SELECT count(*) FROM stocks')" == 1 ]]
if err=$(role_sql screener_reader "INSERT INTO stocks(ts_code,name) VALUES('DENIED','fixture')" 2>&1); then exit 1; fi
[[ "$err" == *"permission denied for table stocks"* ]]
role_sql screener_writer "BEGIN; INSERT INTO stocks(ts_code,name) VALUES('WRITE_OK','fixture'); ROLLBACK;"
if err=$(role_sql screener_writer "DELETE FROM stocks WHERE ts_code='000001.SZ'" 2>&1); then exit 1; fi
[[ "$err" == *"permission denied for table stocks"* ]]
echo 'PASS: actual reader SELECT, reader INSERT denied, writer INSERT and rollback, writer DELETE denied'
direct verify --approved-target-read
expect_block PREVIOUS_RESTORE direct restore --approved-target-read --approved-final-restore
[[ "$(fingerprint)" == "$before" ]]
[[ "$(echo "SELECT count(*) FROM pg_database WHERE datname='ashare_migration';" | admin postgres)" == 0 ]]
[[ "$(echo "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ashare_mig_%';" | admin postgres)" == 0 ]]
[[ "$(docker inspect --format '{{len .NetworkSettings.Networks}}' "$cid")" == 2 ]]
echo 'PASS: full direct restore and verification on reviewed dual-network fixture'
echo 'PASS: standard direct restore, full row hashes, existing ACLs, no repeat restore or new database/roles; ashare_test unchanged'
