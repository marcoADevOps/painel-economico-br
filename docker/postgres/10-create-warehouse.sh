#!/usr/bin/env bash
# Runs only on the first start of an empty Postgres volume.
# Creates the warehouse database and its owner, separate from Airflow metadata.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v wh_db="$WAREHOUSE_DB" -v wh_user="$WAREHOUSE_USER" -v wh_password="$WAREHOUSE_PASSWORD" <<'SQL'
CREATE ROLE :"wh_user" LOGIN PASSWORD :'wh_password';
CREATE DATABASE :"wh_db" OWNER :"wh_user";
SQL
