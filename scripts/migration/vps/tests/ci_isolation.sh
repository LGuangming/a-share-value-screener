#!/usr/bin/env bash
# GitHub disposable runner only. Never run against VPS or a connected Docker context.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]] || exit 64
unset DOCKER_HOST DOCKER_CONTEXT PGSERVICE PGHOSTADDR PGPASSWORD PGOPTIONS
export DOCKER_HOST=unix:///var/run/docker.sock
root=/opt/ashare/migration
[[ ! -e /opt/ashare && ! -L /opt/ashare ]]
! getent passwd ashare-migrate >/dev/null
sudo install -d -m 0755 /opt/ashare
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin ashare-migrate
# Hosted images can grant write access on /opt to the runner. Record and test
# the refusal first; normalize only this disposable runner's parent metadata.
stat -c 'fixture-parent %n owner=%u group=%g mode=%a' / /opt /opt/ashare
df -hP /opt/ashare
getent passwd ashare-migrate | cut -d: -f1,3,4,7
if ! sudo /usr/bin/python3 -c 'import sys;sys.path.insert(0,"scripts/migration/vps");from prepare import require_parent,ROOT;require_parent(ROOT)' 2>/dev/null; then
  result=0
  output=$(sudo /usr/bin/python3 scripts/migration/vps/prepare.py --approved-local-prepare 2>&1) || result=$?
  [[ "$result" == 1 && "$output" == "FAILED: UNSAFE_PARENT" && ! -e "$root" ]]
  echo "PASS: insecure fixture parent refused"
  [[ "$(stat -c %u /)" == 0 && ! -L /opt && -d /opt ]]
  sudo chown root:root /opt
  sudo chmod go-w /opt
fi
sudo /usr/bin/python3 scripts/migration/vps/prepare.py --approved-local-prepare
if sudo /usr/bin/python3 scripts/migration/vps/prepare.py --approved-local-prepare; then exit 1; fi
sudo -u ashare-migrate test -w "$root/archive"
sudo -u ashare-migrate test -r "$root/config/client/owner.pgpass"
sudo -u ashare-migrate test ! -w "$root/bin"
sudo -u ashare-migrate test ! -r "$root/config/private/db-secrets/admin"
sudo -u nobody test ! -r "$root/config/client/owner.pgpass"
sudo -u nobody test ! -r "$root/config/private/db-secrets/reader"
sudo -u nobody test ! -w "$root/ops-public"
sudo -u nobody test ! -w "$root/archive"
sudo -u nobody env SSH_ORIGINAL_COMMAND=data-capacity "$root/bin/ops_dispatch.py"
for cmd in 'whoami' 'data-capacity ' 'data-inventory;id' 'migration-status /etc/shadow' 'stop'; do
  rc=0
  output=$(sudo -u nobody env "SSH_ORIGINAL_COMMAND=$cmd" "$root/bin/ops_dispatch.py") || rc=$?
  [[ "$rc" == 64 && "$output" == 'ERROR: Operation not permitted' ]]
done
now=$(date +%s)
printf '{"captured_at_epoch":%s,"passed":true,"checks":3,"failed_checks":0}\n' "$now" | sudo /usr/bin/python3 "$root/bin/control.py" publish-summary --summary migration-verify
sudo -u nobody env SSH_ORIGINAL_COMMAND=migration-verify "$root/bin/ops_dispatch.py"
printf '{"captured_at_epoch":0,"passed":true,"checks":3,"failed_checks":0}\n' | sudo tee "$root/ops-public/migration-verify.json" >/dev/null
if sudo -u nobody env SSH_ORIGINAL_COMMAND=migration-verify "$root/bin/ops_dispatch.py"; then exit 1; fi
echo 'PASS: filesystem isolation, exact commands and stale evidence denial'
# All Docker operations use the local disposable runner, not a VPS endpoint.
docker pull postgres:18.7-alpine >/dev/null
sudo docker --host unix:///var/run/docker.sock compose -f "$root/compose.yml" config --quiet
sudo docker --host unix:///var/run/docker.sock compose -f "$root/compose.yml" up -d --no-build --pull never --wait
sudo /usr/bin/python3 "$root/bin/control.py" monitor
export PGHOST=127.0.0.1 PGPORT=55432 PGDATABASE=ashare_migration PGSSLMODE=disable
# Host loopback authentication as non-superuser, no production credentials.
ready=0
for i in $(seq 1 30); do
  if sudo -u ashare-migrate env PGHOST="$PGHOST" PGPORT="$PGPORT" PGDATABASE="$PGDATABASE" PGSSLMODE=disable PGUSER=migration_owner PGPASSFILE="$root/config/client/owner.pgpass" "$(command -v psql)" -X -w -At -v ON_ERROR_STOP=1 -c "SELECT current_user||'|'||current_database();" 2>/dev/null | grep -Fxq 'migration_owner|ashare_migration'; then
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo 'FAILED: authenticated host loopback readiness'; exit 1; }
echo 'PASS: authenticated host loopback readiness'
# Actual schema bootstrap as database owner, not superuser; role ACL tool tested separately.
sudo env PGHOST="$PGHOST" PGPORT="$PGPORT" PGDATABASE="$PGDATABASE" PGSSLMODE=disable PGUSER=migration_owner PGPASSFILE="$root/config/client/owner.pgpass" "$(command -v psql)" -X -v ON_ERROR_STOP=1 -f database/neon-schema.sql
sudo env PGHOST="$PGHOST" PGPORT="$PGPORT" PGDATABASE="$PGDATABASE" PGSSLMODE=disable PGUSER=migration_owner PGPASSFILE="$root/config/client/owner.pgpass" "$(command -v python)" scripts/migration/permissions.py apply --policy scripts/migration/tests/production-policy.json --root "$root/verification" --approved-isolated-acl
for role in reader writer; do
  sudo env PGHOST="$PGHOST" PGPORT="$PGPORT" PGDATABASE="$PGDATABASE" PGSSLMODE=disable PGUSER="screener_$role" PGPASSFILE="$root/config/private/db-secrets/$role.pgpass" "$(command -v psql)" -X -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM stocks;'
  # No password lookup failure masquerading as denied CONNECT: use same role password
  # via an isolated root-only wildcard pgpass created from fixture credentials.
  pass=$(sudo cat "$root/config/private/db-secrets/$role")
  printf '127.0.0.1:55432:*:screener_%s:%s\n' "$role" "$pass" | sudo tee "$root/config/private/cross-db-$role.pgpass" >/dev/null
  unset pass
  sudo chmod 600 "$root/config/private/cross-db-$role.pgpass"
  if sudo env PGHOST="$PGHOST" PGPORT="$PGPORT" PGDATABASE=postgres PGSSLMODE=disable PGUSER="screener_$role" PGPASSFILE="$root/config/private/cross-db-$role.pgpass" "$(command -v psql)" -X -w -v ON_ERROR_STOP=1 -c 'SELECT 1'; then exit 1; fi
done
if sudo env PGHOST="$PGHOST" PGPORT="$PGPORT" PGDATABASE="$PGDATABASE" PGSSLMODE=disable PGUSER=screener_reader PGPASSFILE="$root/config/private/db-secrets/reader.pgpass" "$(command -v psql)" -X -v ON_ERROR_STOP=1 -c "INSERT INTO stocks(ts_code,name) VALUES ('000001.SZ','fixture');"; then exit 1; fi
sudo env PGHOST="$PGHOST" PGPORT="$PGPORT" PGDATABASE="$PGDATABASE" PGSSLMODE=disable PGUSER=screener_writer PGPASSFILE="$root/config/private/db-secrets/writer.pgpass" "$(command -v psql)" -X -v ON_ERROR_STOP=1 -c "INSERT INTO stocks(ts_code,name) VALUES ('000001.SZ','fixture');"
echo 'PASS: non-superuser ownership, real role logins and cross-database denial'
# Stop only the verified container; no volume deletion and no production container.
sentinel=$(docker create --name ashare-b4-sentinel postgres:18.7-alpine)
cid=$(docker inspect --format '{{.Id}}' ashare-migration-postgres)
if sudo /usr/bin/python3 "$root/bin/control.py" stop; then exit 1; fi
sudo /usr/bin/python3 "$root/bin/control.py" stop --approved-isolated-stop
[[ "$(docker inspect --format '{{.Id}} {{.State.Running}}' ashare-migration-postgres)" == "$cid false" ]]
[[ "$(docker inspect --format '{{.Id}}' ashare-b4-sentinel)" == "$sentinel" ]]
docker volume inspect ashare-migration-pgdata >/dev/null
docker start "$cid" >/dev/null
for i in $(seq 1 30); do
  if docker exec "$cid" pg_isready -h 127.0.0.1 -U postgres -d ashare_migration >/dev/null 2>&1; then break; fi
  sleep 1
done
sudo env PGHOST="$PGHOST" PGPORT="$PGPORT" PGDATABASE="$PGDATABASE" PGSSLMODE=disable PGUSER=screener_reader PGPASSFILE="$root/config/private/db-secrets/reader.pgpass" "$(command -v psql)" -X -v ON_ERROR_STOP=1 -At -c "SELECT name FROM stocks WHERE ts_code='000001.SZ'" | grep -Fx fixture
sudo /usr/bin/python3 "$root/bin/control.py" stop --approved-isolated-stop
echo 'PASS: isolated stop, retained data, sentinel unchanged and restart recovery'
