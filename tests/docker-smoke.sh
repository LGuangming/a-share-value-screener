#!/usr/bin/env bash
set -euo pipefail
# Disposable runner only. No registry publication or production settings.
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo "Run only on isolated GitHub Actions runner"; exit 2; }
root="/opt/ashare/test"
[[ ! -e "$root" ]] || { echo "Fixture root must not exist"; exit 2; }
sudo install -d -m 0750 -o "$(id -u)" -g "$(id -g)" "$root"
mkdir -p "$root/config"
export COMPOSE_PROJECT_NAME=ashare-ci
export WEB_IMAGE=ashare-web:ci WORKER_IMAGE=ashare-worker:ci
export DATABASE_NETWORK=ashare-ci-database ASHARE_LOOPBACK_PORT=3198
unset PROXY_NETWORK
export ARCHIVE_DIR="$root/archive" WEB_CACHE_DIR="$root/cache/nextjs" WORKER_TMP_DIR="$root/tmp"
export WEB_ENV_FILE="$root/config/web.env" WORKER_ENV_FILE="$root/config/worker.env"
export DOCKER_FIXTURE_PASSWORD="$(openssl rand -hex 12)"
secret="$(openssl rand -hex 32)"
mkdir -p "$ARCHIVE_DIR" "$WEB_CACHE_DIR" "$WORKER_TMP_DIR/duckdb"
sudo chown -R 10002:10010 "$ARCHIVE_DIR" "$WORKER_TMP_DIR"
sudo chmod 2750 "$ARCHIVE_DIR"
sudo chmod 750 "$WORKER_TMP_DIR" "$WORKER_TMP_DIR/duckdb"
sudo chown 10001:10010 "$WEB_CACHE_DIR"
sudo chmod 750 "$WEB_CACHE_DIR"
umask 077
printf 'DATABASE_READ_URL=postgresql://screener_reader:fixture_only@postgres:5432/ashare_test\nSITE_ACCESS_PASSWORD=%s\nSITE_SESSION_SECRET=%s\n' "$DOCKER_FIXTURE_PASSWORD" "$secret" >"$WEB_ENV_FILE"
printf 'DATABASE_URL=postgresql://screener_writer:fixture_only@postgres:5432/ashare_test\nARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY=false\n' >"$WORKER_ENV_FILE"
unset secret
dc=(docker compose -p ashare-ci -f compose.yaml -f compose.loopback.yaml -f compose.vps-test.yaml -f compose.test.yaml)
cleanup() {
  "${dc[@]}" down -v --remove-orphans || true
  docker network rm "$DATABASE_NETWORK" >/dev/null 2>&1 || true
  sudo rm -rf -- "$root"
}
trap cleanup EXIT
docker network create --internal "$DATABASE_NETWORK"
"${dc[@]}" --profile worker config --quiet
"${dc[@]}" up -d --wait postgres
"${dc[@]}" exec -T postgres psql -U fixture_admin -d ashare_test -v ON_ERROR_STOP=1 < database/schema.sql
"${dc[@]}" exec -T postgres psql -U fixture_admin -d ashare_test -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE screener_reader LOGIN PASSWORD 'fixture_only';
CREATE ROLE screener_writer LOGIN PASSWORD 'fixture_only';
GRANT CONNECT ON DATABASE ashare_test TO screener_reader,screener_writer;
GRANT USAGE ON SCHEMA public TO screener_reader,screener_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO screener_reader;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO screener_writer;
SQL
# Reject production DB before starting any business process.
if "${dc[@]}" run --rm --no-deps -e DATABASE_URL=postgresql://screener_writer:fixture_only@postgres:5432/ashare worker --help; then
  echo "Worker guard unexpectedly accepted production database"; exit 1
fi
if "${dc[@]}" run --rm --no-deps -e DATABASE_READ_URL=postgresql://screener_reader:fixture_only@postgres:5432/ashare web; then
  echo "Web guard unexpectedly accepted production database"; exit 1
fi
"${dc[@]}" run --rm worker --help
"${dc[@]}" run --rm --entrypoint python worker /fixtures/docker-seed.py
"${dc[@]}" up -d --wait web
"${dc[@]}" exec -T web node -e 'const a=require("node:assert/strict"),fs=require("node:fs");a.equal(process.getuid(),10001);a.equal(process.getgid(),10010);a.ok(!process.env.DATABASE_URL&&!process.env.TUSHARE_TOKEN);a.throws(()=>fs.writeFileSync("/data/archive/forbidden","x"));'
"${dc[@]}" exec -T web node -e 'const a=require("node:assert/strict"),{Client}=require("pg");(async()=>{const c=new Client({connectionString:process.env.DATABASE_READ_URL});await c.connect();try{const r=await c.query("SELECT has_table_privilege(current_user,\x27stocks\x27,\x27UPDATE\x27) AS can_write");a.equal(r.rows[0].can_write,false);await a.rejects(c.query("UPDATE stocks SET name=\x27forbidden\x27"),/permission denied/);}finally{await c.end()}})().catch(e=>{console.error(e);process.exitCode=1})'
node tests/docker-smoke.cjs
ASHARE_TEST_ALLOW_STALE=1 ASHARE_READINESS_URL=http://127.0.0.1:3198 ASHARE_READINESS_PASSWORD="$DOCKER_FIXTURE_PASSWORD" ASHARE_READINESS_STOCK=000001.SZ ASHARE_READINESS_QUERY=Docker ASHARE_READINESS_YEAR=2025 node deploy/check-readiness.cjs
"${dc[@]}" up -d --wait --force-recreate web
node tests/docker-smoke.cjs
echo "Container recreation preserves SQL and archive state"
