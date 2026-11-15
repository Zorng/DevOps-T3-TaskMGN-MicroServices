#!/bin/sh
set -eu

# The official image sources non-executable initialization scripts too.
# Use psql's quoted variable substitution; never interpolate passwords into SQL.
psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_password IDENTITY_APP_PASSWORD
CREATE ROLE identity_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
ALTER DATABASE identity_db OWNER TO identity_app;
REVOKE ALL ON DATABASE identity_db FROM PUBLIC;
GRANT CONNECT ON DATABASE identity_db TO identity_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
unset IDENTITY_APP_PASSWORD
