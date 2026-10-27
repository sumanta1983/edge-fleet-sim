#!/bin/sh
# Runs once, when the Postgres data volume is first created.
#
# Three roles, least privilege:
#   edgefleet_owner     owns the schema; used only by migrations and seeding
#   edgefleet_api       used by the REST API
#   edgefleet_ingestor  used by the MQTT ingestor
# The two app roles are NOT superusers, do NOT have BYPASSRLS and do NOT own
# the tables, so row-level security always applies to them.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE edgefleet_owner    LOGIN PASSWORD '$DB_OWNER_PASSWORD';
CREATE ROLE edgefleet_api      LOGIN PASSWORD '$DB_API_PASSWORD'      NOSUPERUSER NOBYPASSRLS;
CREATE ROLE edgefleet_ingestor LOGIN PASSWORD '$DB_INGESTOR_PASSWORD' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE edgefleet OWNER edgefleet_owner;
REVOKE ALL ON DATABASE edgefleet FROM PUBLIC;
GRANT CONNECT ON DATABASE edgefleet TO edgefleet_api, edgefleet_ingestor;
SQL
