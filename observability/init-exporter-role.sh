#!/bin/sh
# Runs when the database volume is first initialized. An existing volume never
# runs this; the same statements must be applied to it once by hand.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
CREATE ROLE postgres_exporter LOGIN PASSWORD '${POSTGRES_EXPORTER_PASSWORD}';
GRANT pg_monitor TO postgres_exporter;
SQL
