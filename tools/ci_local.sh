#!/usr/bin/env bash
# Run exactly what .github/workflows/ci.yml runs, in the same order.
#
# Written after CI sat red for 23 runs across two episodes while every commit
# message claimed the gate was green. The cause was not the failing check - it
# was that "the static gate passes" is ONE of four steps, and running one by
# hand and reporting the build as green is the false-success shape this repo
# hunts everywhere else.
#
# If a step is added to ci.yml it must be added here, or this becomes the same
# lie in a shorter form.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
PY="${PY:-venv/bin/python3}"
rc=0
step () {
  printf '  %-34s' "$1"; shift
  if out=$("$@" 2>&1); then echo "ok"; else
    echo "FAIL"; echo "$out" | sed 's/^/       /' | head -12; rc=1
  fi
}
echo "CI steps, in ci.yml order:"
step "compileall"                    "$PY" -m compileall -q -x 'backup_|quarantine/' .
step "ruff E9,F63,F7,F82"            venv/bin/ruff check --select=E9,F63,F7,F82 \
                                       --exclude backup_20260621_053644,quarantine .
step "check_inherited --static"      "$PY" tools/check_inherited.py --static
step "check_shell_version"           "$PY" tools/check_shell_version.py
step "check_routing"                 "$PY" tools/check_routing.py
step "check_sandbox"                 "$PY" tools/check_sandbox.py
echo
if [ "$rc" -eq 0 ]; then echo "ALL GREEN - this is what CI will see."
else echo "RED - do not claim the build passes."; fi
exit "$rc"
