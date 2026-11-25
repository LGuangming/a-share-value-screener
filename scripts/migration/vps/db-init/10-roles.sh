#!/usr/bin/env bash
# PostgreSQL official entrypoint runs this as postgres, only for a new volume.
set -euo pipefail
[[ "$POSTGRES_DB" == ashare_migration && "$POSTGRES_USER" == postgres ]] || exit 64
# Root-controlled files are readable only inside this isolated container and
# through a private root-only host directory. No passwords in argv or logs.
export MIG_OWNER_PASSWORD="$(cat /run/migration-secrets/owner)"
export MIG_READER_PASSWORD="$(cat /run/migration-secrets/reader)"
export MIG_WRITER_PASSWORD="$(cat /run/migration-secrets/writer)"
for name in MIG_OWNER_PASSWORD MIG_READER_PASSWORD MIG_WRITER_PASSWORD; do
  [[ "${!name}" =~ ^[a-f0-9]{64}$ ]] || exit 65
done
psql -X --no-password --set ON_ERROR_STOP=1 --username postgres --dbname ashare_migration <<'SQL'
\set VERBOSITY terse
\getenv owner_pw MIG_OWNER_PASSWORD
\getenv reader_pw MIG_READER_PASSWORD
\getenv writer_pw MIG_WRITER_PASSWORD
BEGIN;
SET LOCAL log_statement = 'none';
SET LOCAL log_min_error_statement = 'panic';
CREATE ROLE migration_owner LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD :'owner_pw';
CREATE ROLE screener_reader LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8 PASSWORD :'reader_pw';
CREATE ROLE screener_writer LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8 PASSWORD :'writer_pw';
ALTER DATABASE ashare_migration OWNER TO migration_owner;
REVOKE CONNECT ON DATABASE postgres, template1 FROM PUBLIC;
REVOKE ALL ON DATABASE ashare_migration FROM PUBLIC;
GRANT CONNECT ON DATABASE ashare_migration TO screener_reader, screener_writer;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
COMMIT;
SQL
unset MIG_OWNER_PASSWORD MIG_READER_PASSWORD MIG_WRITER_PASSWORD
