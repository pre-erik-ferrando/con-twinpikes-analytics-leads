#!/usr/bin/env bash
# run_tests.sh — every check that needs no ClickHouse, no credentials and no network.
#
# Usage:
#   ./run_tests.sh            # everything
#   ./run_tests.sh -v         # any extra flag is passed through to pytest
#   ./run_tests.sh --install  # pip install the test deps before running
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTEST_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --install)
      python -m pip install -r dwh/transf_dbt/dev-requirements.txt -r dwh/transf_py/leadgen/requirements.txt
      ;;
    *) PYTEST_ARGS+=("$arg") ;;
  esac
done

printf '\n\033[1;36m==> pytest (no DB): engine + dbt syntax/refs + docs\033[0m\n'
python -m pytest "${PYTEST_ARGS[@]}"

N_SINGULAR=$(find dwh/transf_dbt/tests -name 'assert_*.sql' | wc -l | tr -d ' ')
printf '\n\033[1;32m✓ Suite with no DB is green.\033[0m\n'
printf '\033[0;36mℹ  Not covered: the %s singular dbt guards (assert_*.sql), which need ClickHouse:\n' "$N_SINGULAR"
printf '   python leadgen.py dbt -- test\033[0m\n'
