#!/bin/sh
set -eu
psql -X -q -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
\getenv app_password EPS_DB_PASSWORD
\getenv exporter_password EPS_EXPORTER_PASSWORD
SELECT format('CREATE ROLE eps LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', :'app_password') \gexec
SELECT format('CREATE ROLE eps_exporter LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', :'exporter_password') \gexec
ALTER DATABASE eps OWNER TO eps;
REVOKE ALL ON DATABASE eps FROM PUBLIC;
GRANT CONNECT ON DATABASE eps TO eps_exporter;
GRANT pg_monitor TO eps_exporter;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
