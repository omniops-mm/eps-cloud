#!/bin/sh
# Runs when the database volume is first initialized. An existing volume never
# runs this; the same statements must be applied to it once by hand.
set -eu
: "${POSTGRES_EXPORTER_PASSWORD:?POSTGRES_EXPORTER_PASSWORD is required}"
psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
\getenv exporter_password POSTGRES_EXPORTER_PASSWORD
SELECT format('CREATE ROLE postgres_exporter LOGIN PASSWORD %L', :'exporter_password') \gexec
GRANT pg_monitor TO postgres_exporter;
SQL
