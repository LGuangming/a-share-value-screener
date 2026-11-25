#!/usr/bin/env bash
# Disposable Actions runner only. Refuse execution outside an Actions job.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${PGHOST:-}" == 127.0.0.1 && "${PGUSER:-}" == postgres && "${PGSSLMODE:-}" == disable ]] || exit 64
[[ "$FIXTURE_ROWS" =~ ^(120000|600000|1200000)$ ]] || exit 64
umask 077
export PGPASSFILE="$RUNNER_TEMP/pressure.pgpass"
printf '127.0.0.1:55441:*:postgres:fixture-only-password\n127.0.0.1:55442:*:postgres:fixture-only-password\n127.0.0.1:55442:ashare_migration:screener_reader:fixture-only-password\n127.0.0.1:55442:ashare_migration:screener_writer:fixture-only-password\n' > "$PGPASSFILE"
disk_samples="$RUNNER_TEMP/pressure-disk-samples"
disk_baseline=$(df -B1 --output=avail "$RUNNER_TEMP" | tail -1 | tr -d ' ')
(while true; do df -B1 --output=avail "$RUNNER_TEMP" | tail -1 | tr -d ' '; sleep 1; done) > "$disk_samples" &
sampler=$!
finish() {
  kill "$sampler" 2>/dev/null || true
  wait "$sampler" 2>/dev/null || true
  rm -f "$PGPASSFILE"
}
trap finish EXIT
container_evidence() {
  local side=$1 name="migration-pressure-$1"
  echo "PRESSURE_DB_CONTAINER $side phase=$2"
  docker inspect --format='memory_limit={{.HostConfig.Memory}} nano_cpus={{.HostConfig.NanoCpus}} oom_killed={{.State.OOMKilled}} restart_count={{.RestartCount}}' "$name"
  [[ "$(docker inspect --format='{{.State.OOMKilled}} {{.RestartCount}}' "$name")" == "false 0" ]]
  # cgroup v2 includes kills of postgres child processes, not only PID 1.
  docker exec "$name" sh -ec '
    test -r /sys/fs/cgroup/memory.events
    test -r /sys/fs/cgroup/memory.peak
    printf "memory_peak_bytes="; cat /sys/fs/cgroup/memory.peak
    cat /sys/fs/cgroup/memory.events
    awk '"'"'$1=="oom" {o=$2; seen++} $1=="oom_kill" {k=$2; seen++} END {exit !(seen==2 && o==0 && k==0)}'"'"' /sys/fs/cgroup/memory.events
    grep -E "^(anon|file|kernel) " /sys/fs/cgroup/memory.stat
    du -sk "$PGDATA"
  '
}
for spec in "source 55441 ashare_source" "target 55442 ashare_migration"; do
  read -r side port db <<< "$spec"
  docker run -d --name "migration-pressure-$side" --cpus=1.5 --memory=800m --memory-swap=800m \
    --shm-size=256m -p "127.0.0.1:$port:5432" \
    -e POSTGRES_PASSWORD=fixture-only-password -e "POSTGRES_DB=$db" postgres:18.7-alpine \
    -c shared_buffers=192MB -c work_mem=4MB -c max_connections=30 -c idle_in_transaction_session_timeout=60s \
    -c maintenance_work_mem=64MB -c max_parallel_maintenance_workers=1 >/dev/null
  # The official image starts and stops a temporary Unix-socket-only server.
  # Check authenticated TCP on the published loopback port, not that socket.
  ready=0
  for i in $(seq 1 60); do
    if PGPORT="$port" PGDATABASE="$db" psql -X -w -At -v ON_ERROR_STOP=1 -c 'SELECT 1' 2>/dev/null | grep -Fxq 1; then
      ready=1
      break
    fi
    sleep 1
  done
  [[ "$ready" == 1 ]] || { echo "FAILED: fixture PostgreSQL TCP readiness"; exit 1; }
done
# pg_dump can run for >60s while exporting a snapshot; backup transaction
# remains idle during the child dump. The tool sets transaction-local timeout only.
export PGPORT=55441 PGDATABASE=ashare_source
psql -v ON_ERROR_STOP=1 -f database/neon-schema.sql
psql -v ON_ERROR_STOP=1 -v fixture_rows="$FIXTURE_ROWS" -f scripts/migration/tests/pressure_fixture.sql
psql -At -c "SELECT 'PRESSURE_DB_SOURCE_BYTES '||pg_database_size(current_database());"
psql -At -c "SELECT name||'='||setting||coalesce(unit,'') FROM pg_settings WHERE name IN ('shared_buffers','work_mem','max_connections','idle_in_transaction_session_timeout','maintenance_work_mem') ORDER BY name;"
root="$RUNNER_TEMP/pressure-evidence"
echo "PRESSURE_DB_TIER $FIXTURE_TIER"
echo "PRESSURE_DB_PHASE backup"
/usr/bin/time -v python scripts/migration/postgres.py backup --root "$root"
du -sb "$root"
export PGPORT=55442 PGDATABASE=ashare_migration
echo "PRESSURE_DB_PHASE restore_serial"
/usr/bin/time -v python scripts/migration/postgres.py restore --root "$root" --approved-isolated-restore
echo "PRESSURE_DB_PHASE verify_serial"
/usr/bin/time -v python scripts/migration/postgres.py verify --root "$root"
psql -At -c "SELECT 'PRESSURE_DB_TARGET_BYTES '||pg_database_size(current_database());"
container_evidence target serial_complete
psql -v ON_ERROR_STOP=1 -c "CREATE ROLE screener_reader LOGIN CONNECTION LIMIT 8 PASSWORD 'fixture-only-password'; CREATE ROLE screener_writer LOGIN CONNECTION LIMIT 8 PASSWORD 'fixture-only-password';"
python scripts/migration/permissions.py apply --policy scripts/migration/tests/production-policy.json --root "$root" --approved-isolated-acl
PGUSER=screener_reader psql -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM screen_results;'
if PGUSER=screener_reader psql -v ON_ERROR_STOP=1 -c "UPDATE daily_prices SET close=1 WHERE false"; then exit 1; fi
PGUSER=screener_writer psql -v ON_ERROR_STOP=1 -c "BEGIN; UPDATE daily_prices SET close=1 WHERE false; DELETE FROM screen_results WHERE false; ROLLBACK;"
if PGUSER=screener_writer psql -v ON_ERROR_STOP=1 -c 'DELETE FROM stocks WHERE false'; then exit 1; fi
# Only disposable runner DB; parallel restore is non-atomic and benchmark-only.
[[ "$PGPORT" == 55442 && "$PGDATABASE" == ashare_migration ]]
export PGDATABASE=postgres
psql -v ON_ERROR_STOP=1 -c 'DROP DATABASE ashare_migration'
psql -v ON_ERROR_STOP=1 -c 'CREATE DATABASE ashare_migration'
export PGDATABASE=ashare_migration
echo "PRESSURE_DB_PHASE restore_parallel_2_nonatomic_fixture_only"
/usr/bin/time -v pg_restore --jobs=2 --exit-on-error --no-owner --no-acl --dbname=ashare_migration "$root/source.dump"
python scripts/migration/postgres.py verify --root "$root"
python scripts/migration/permissions.py apply --policy scripts/migration/tests/production-policy.json --root "$root" --approved-isolated-acl
for side in source target; do container_evidence "$side" parallel_complete; done
# Small tier tests final generation after source changes, not logical replication.
if [[ "$FIXTURE_ROWS" == 120000 ]]; then
  export PGPORT=55441 PGDATABASE=ashare_source
  psql -v ON_ERROR_STOP=1 -c "UPDATE stocks SET name='fixture-updated' WHERE ts_code='000001.SZ'"
  echo "PRESSURE_DB_PHASE final_backup"
  /usr/bin/time -v python scripts/migration/postgres.py backup --root "$RUNNER_TEMP/final-generation"
  export PGPORT=55442 PGDATABASE=ashare_migration
  [[ "$PGPORT" == 55442 && "$PGDATABASE" == ashare_migration ]]
  export PGDATABASE=postgres
  psql -v ON_ERROR_STOP=1 -c 'DROP DATABASE ashare_migration'
  psql -v ON_ERROR_STOP=1 -c 'CREATE DATABASE ashare_migration'
  export PGDATABASE=ashare_migration
  echo "PRESSURE_DB_PHASE final_restore"
  /usr/bin/time -v python scripts/migration/postgres.py restore --root "$RUNNER_TEMP/final-generation" --approved-isolated-restore
  echo "PRESSURE_DB_PHASE final_verify"
  /usr/bin/time -v python scripts/migration/postgres.py verify --root "$RUNNER_TEMP/final-generation"
  python scripts/migration/permissions.py apply --policy scripts/migration/tests/production-policy.json --root "$RUNNER_TEMP/final-generation" --approved-isolated-acl
  [[ "$(psql -At -c "SELECT name FROM stocks WHERE ts_code='000001.SZ'")" == fixture-updated ]]
  echo 'PASS: final full snapshot after source change'
fi
for side in source target; do container_evidence "$side" final; done
disk_min=$(sort -n "$disk_samples" | head -1)
[[ "$disk_baseline" =~ ^[0-9]+$ && "$disk_min" =~ ^[0-9]+$ ]]
echo "PRESSURE_DB_RUNNER_DISK_SAMPLED baseline_free_bytes=$disk_baseline minimum_free_bytes=$disk_min peak_delta_bytes=$((disk_baseline>disk_min ? disk_baseline-disk_min : 0)) sample_interval_seconds=1"
echo 'PASS: constrained database pressure and recovery'
