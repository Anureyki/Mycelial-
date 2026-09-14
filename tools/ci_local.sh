#!/usr/bin/env bash
# Run exactly what .github/workflows/ci.yml runs, in the same order, IN AN
# ENVIRONMENT BUILT THE SAME WAY.
#
# Written after CI sat red for 23 runs across two episodes while every commit
# message claimed the gate was green. The cause was not the failing check - it
# was that "the static gate passes" is ONE of several steps, and running one by
# hand and reporting the build as green is the false-success shape this repo
# hunts everywhere else.
#
# THEN IT HAPPENED AGAIN, TEN RUNS, AND THIS SCRIPT WAS THE THING SAYING IT.
# 2026-09-11 17:17 to 2026-09-12 00:44: every gate passed here and five failed
# on the runner. Not a missing step - a missing MODULE. This ran in
# venv/bin/python3, where the whole project is installed; CI installed `ruff`
# and nothing else, so the first gate to `import requests` died on it and the
# four behind it never ran. The line printed underneath was "ALL GREEN - this
# is what CI will see."
#
# Same steps, different environment is the same lie with the evidence one layer
# further down. So the environment is now part of the reproduction: this builds
# a venv from ci-requirements.txt ALONE - the same file ci.yml installs - and
# runs the gates inside it. A gate that grows a dependency CI does not install
# now fails HERE, first, which is the only place a failure is cheap.
#
# Five were missing when this was written: requests, cryptography, paho-mqtt,
# flask, waitress.
#
# If a step is added to ci.yml it must be added here, or this becomes the same
# lie in a shorter form.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

REQ="ci-requirements.txt"
CIVENV="${CIVENV:-state/ci-venv}"
STAMP="$CIVENV/.requirements-stamp"

if [ -n "${PY:-}" ]; then
  # An explicit override is allowed and is NOT the CI environment. Say so
  # rather than printing the same confident line over a different measurement.
  HONEST="ran against \$PY=$PY, which is NOT how CI is provisioned"
else
  if [ ! -x "$CIVENV/bin/python3" ] || [ "$REQ" -nt "$STAMP" ]; then
    echo "building a CI-matching venv from $REQ (this is the point of the script)"
    rm -rf "$CIVENV"
    python3 -m venv "$CIVENV" >/dev/null || exit 2
    "$CIVENV/bin/pip" -q install -r "$REQ" || exit 2
    touch "$STAMP"
  fi
  PY="$CIVENV/bin/python3"
  RUFF="$CIVENV/bin/ruff"
  HONEST="this is what CI will see - same steps, same dependency set"
fi
RUFF="${RUFF:-$(dirname "$PY")/ruff}"

rc=0
step () {
  printf '  %-34s' "$1"; shift
  if out=$("$@" 2>&1); then echo "ok"; else
    echo "FAIL"; echo "$out" | sed 's/^/       /' | head -12; rc=1
  fi
}
echo "CI steps, in ci.yml order:"
step "compileall"                    "$PY" -m compileall -q -x 'backup_|quarantine/' .
step "ruff E9,F63,F7,F82"            "$RUFF" check --select=E9,F63,F7,F82 \
                                       --exclude backup_20260621_053644,quarantine .
step "check_inherited --static"      "$PY" tools/check_inherited.py --static
step "check_shell_version"           "$PY" tools/check_shell_version.py
step "check_routing"                 "$PY" tools/check_routing.py
step "check_sandbox"                 "$PY" tools/check_sandbox.py
step "check_acl"                     "$PY" tools/check_acl.py
step "check_eval"                    "$PY" tools/check_eval.py
step "check_drift"                   "$PY" tools/check_drift.py
step "check_accounts"                "$PY" tools/check_accounts.py
step "check_retrieval"               "$PY" tools/check_retrieval.py
step "check_core_interface"         "$PY" tools/check_core_interface.py
step "check_financial_authority"    "$PY" tools/check_financial_authority.py
step "check_asset_registry"         "$PY" tools/check_asset_registry.py
step "check_ontology"               "$PY" tools/check_ontology.py
step "check_ingest"                 "$PY" tools/check_ingest.py
step "check_contracts"              "$PY" tools/check_contracts.py
step "check_no_secrets"             "$PY" tools/check_no_secrets.py
step "check_fs_boundary"            "$PY" tools/check_fs_boundary.py
step "check_staging_boundary"       "$PY" tools/check_staging_boundary.py
step "check_history_classes"        "$PY" tools/check_history_classes.py
step "check_case_law"               "$PY" tools/check_case_law.py
step "check_contract_engine"        "$PY" tools/check_contract_engine.py
step "check_custody"                "$PY" tools/check_custody.py
step "check_spoken_reading"         "$PY" tools/check_spoken_reading.py
step "check_dispute_letters"        "$PY" tools/check_dispute_letters.py
echo
if [ "$rc" -eq 0 ]; then echo "ALL GREEN - $HONEST."
else echo "RED - do not claim the build passes."; fi
exit "$rc"
