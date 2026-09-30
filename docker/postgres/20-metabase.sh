#!/usr/bin/env bash
# Metabase roles. Runs on the first start of an empty Postgres volume; for an
# existing volume run it once by hand (docs/setup-vm.md). Idempotent.
#  - metabase:    owns the "metabase" database (Metabase's own settings)
#  - metabase_ro: read-only, marts schema only (what the dashboards query)
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$METABASE_DB_PASSWORD" -v ro_password="$METABASE_READER_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE metabase LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metabase')
\gexec
ALTER ROLE metabase PASSWORD :'app_password';
SELECT 'CREATE DATABASE metabase OWNER metabase'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'metabase')
\gexec
SELECT 'CREATE ROLE metabase_ro LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metabase_ro')
\gexec
ALTER ROLE metabase_ro PASSWORD :'ro_password';
SQL

# Read-only access to marts in the warehouse, including tables created later.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$WAREHOUSE_DB" \
  -v wh_owner="$WAREHOUSE_USER" <<'SQL'
CREATE SCHEMA IF NOT EXISTS marts AUTHORIZATION :"wh_owner";
REVOKE ALL ON SCHEMA public FROM metabase_ro;
GRANT CONNECT ON DATABASE :"DBNAME" TO metabase_ro;
GRANT USAGE ON SCHEMA marts TO metabase_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO metabase_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE :"wh_owner" IN SCHEMA marts GRANT SELECT ON TABLES TO metabase_ro;
SQL
