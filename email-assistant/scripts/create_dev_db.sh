#!/usr/bin/env bash
# Create the local development and test databases with the required extensions.
# Requires a running PostgreSQL 16 and permission to create roles/databases.
set -euo pipefail

DB_USER="${DB_USER:-eaa}"
DB_PASSWORD="${DB_PASSWORD:-devpassword}"
DB_NAME="${DB_NAME:-email_assistant}"
TEST_DB_NAME="${TEST_DB_NAME:-email_assistant_test}"
PSQL="${PSQL:-psql}"

run_as_postgres() {
    if [ "$(id -u)" -eq 0 ] && id postgres >/dev/null 2>&1; then
        su postgres -c "$1"
    else
        eval "$1"
    fi
}

echo "Creating role ${DB_USER}..."
run_as_postgres "${PSQL} -c \"DO \\\$\\\$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}') THEN
        CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}' CREATEDB;
    END IF;
END \\\$\\\$;\"" || true

for db in "${DB_NAME}" "${TEST_DB_NAME}"; do
    echo "Creating database ${db}..."
    run_as_postgres "${PSQL} -c \"CREATE DATABASE ${db} OWNER ${DB_USER};\"" 2>/dev/null || \
        echo "  (already exists)"
    echo "Enabling extensions in ${db}..."
    run_as_postgres "${PSQL} -d ${db} -c \"
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        CREATE EXTENSION IF NOT EXISTS unaccent;
        CREATE EXTENSION IF NOT EXISTS vector;\"" || \
        echo "  (vector unavailable — install postgresql-16-pgvector before phase 3)"
done

echo
echo "Done. Add to .env:"
echo "  DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"
echo "  TEST_DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${TEST_DB_NAME}"
