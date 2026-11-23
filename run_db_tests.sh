#!/usr/bin/env bash
# run_db_tests.sh — the tests that need a real ClickHouse, against a throwaway local server.
#
# Downloads the official ClickHouse binary once into .clickhouse/ (gitignored, ~265 MB, checksum verified),
# starts a server on localhost:18123 (local connections only) with its data in a temporary folder,
# runs dwh/transf_py/leadgen/tests_db
# (engine on ClickHouse + dbt build and tests), then stops the server and deletes the data. It never connects
# to the project's ClickHouse: the DBT_CH_* variables are overridden for this script only.
#
# Usage:
#   ./run_db_tests.sh          # everything
#   ./run_db_tests.sh -k flow  # extra flags go to pytest
#
# In CI the server is a Docker service instead (.github/workflows/tests.yml, job db); same tests.
set -euo pipefail

CH_VERSION="26.10.2.103"   # same series as production (26.10); the exact production build is not published
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
CH_DIR="$SCRIPT_DIR/.clickhouse"
CH_BIN="$CH_DIR/clickhouse-$CH_VERSION"
PORT=18123

if [[ ! -x "$CH_BIN" ]]; then
  mkdir -p "$CH_DIR"
  url="https://github.com/ClickHouse/ClickHouse/releases/download/v${CH_VERSION}-stable"
  tgz="clickhouse-common-static-${CH_VERSION}-amd64.tgz"
  printf '\033[1;36m==> downloading ClickHouse %s (once)\033[0m\n' "$CH_VERSION"
  curl -fsSL -o "$CH_DIR/$tgz" "$url/$tgz"
  curl -fsSL -o "$CH_DIR/$tgz.sha512" "$url/$tgz.sha512"
  (cd "$CH_DIR" && sha512sum -c "$tgz.sha512")
  tar -xzf "$CH_DIR/$tgz" -C "$CH_DIR" --strip-components=3 --wildcards "*/usr/bin/clickhouse"
  mv "$CH_DIR/clickhouse" "$CH_BIN"
  rm -f "$CH_DIR/$tgz" "$CH_DIR/$tgz.sha512"
fi

DATA="$(mktemp -d)"
cleanup() {
  [[ -n "${CH_PID:-}" ]] && kill "$CH_PID" 2>/dev/null && wait "$CH_PID" 2>/dev/null || true
  rm -rf "$DATA"
}
trap cleanup EXIT

printf '\033[1;36m==> starting a throwaway ClickHouse on localhost:%s\033[0m\n' "$PORT"
(cd "$DATA" && "$CH_BIN" server -- --path="$DATA/" --http_port=$PORT \
   --tcp_port=19000 --mysql_port=19004 --postgresql_port=19005 --interserver_http_port=19009 \
   --logger.console=0 --logger.log="$DATA/server.log" --logger.errorlog="$DATA/server.err.log") &
CH_PID=$!
for _ in $(seq 1 60); do
  curl -fs "http://localhost:$PORT/ping" >/dev/null 2>&1 && break
  sleep 1
done
curl -fs "http://localhost:$PORT/ping" >/dev/null || { echo "ClickHouse did not start; log: $DATA/server.err.log"; tail -20 "$DATA/server.err.log" || true; exit 1; }

export DBT_CH_HOST=localhost DBT_CH_PORT=$PORT DBT_CH_USER=default DBT_CH_PASSWORD="" \
       DBT_CH_SCHEMA=fld_landinglgn LEADGEN_TEST_CH=1
printf '\n\033[1;36m==> pytest on ClickHouse: engine + dbt build and tests\033[0m\n'
python -m pytest dwh/transf_py/leadgen/tests_db "$@"
printf '\n\033[1;32m✓ Suite on ClickHouse is green.\033[0m\n'
