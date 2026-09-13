#!/bin/sh
set -eu
umask 077
: "${PGDATA:?}" "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
: "${POSTGRES_PASSWORD:?}" "${EPS_DB_PASSWORD:?}" "${EPS_EXPORTER_PASSWORD:?}"
case "$(postgres --version)" in
    "postgres (PostgreSQL) 16."*) ;;
    *) echo "Expected PostgreSQL 16; refusing initialization." >&2; exit 1 ;;
esac
if [ -f "$PGDATA/.eps-bootstrap-complete" ] && [ -s "$PGDATA/PG_VERSION" ]; then
    [ "$(cat "$PGDATA/PG_VERSION")" = "16" ] || exit 1
    exit 0
fi
if [ -e "$PGDATA/PG_VERSION" ] || [ -e "$PGDATA/.eps-bootstrap-complete" ]; then
    echo "Refusing an existing or interrupted database without a complete EPS bootstrap." >&2
    exit 1
fi
mkdir -p "$PGDATA"
printf '%s\n' "$POSTGRES_PASSWORD" | initdb -D "$PGDATA" --username="$POSTGRES_USER" \
    --pwfile=/dev/stdin --auth-local=trust --auth-host=scram-sha-256
printf '\nhost all all all scram-sha-256\n' >> "$PGDATA/pg_hba.conf"
# The temporary server accepts Unix sockets only inside this pod.
export PGHOST=/var/run/postgresql
export PGUSER="$POSTGRES_USER"
cleanup() { pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
pg_ctl -D "$PGDATA" -o "-c listen_addresses='' -c unix_socket_directories=/var/run/postgresql" -w start
createdb --no-password "$POSTGRES_DB"
/bin/sh "$(dirname "$0")/init-db.sh"
pg_ctl -D "$PGDATA" -m fast -w stop
trap - EXIT HUP INT TERM
# Written only after role creation and a clean stop; interrupted init needs operator recovery.
touch "$PGDATA/.eps-bootstrap-complete"
