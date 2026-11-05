#!/usr/bin/env python3
"""Retire the enrolled release key so a new phone can enrol. A deliberate act at the
server, by hand - enrolment is write-once precisely so nothing else can swap the key.

    python3 tools/reset_principal_key.py --confirm

The old key is moved to state/security/retired/ (kept, never deleted) and the reset
is emitted, so the drift monitor raises it like an enrolment: a reset he did not
make is a reset he hears about.
"""
import os
import shutil
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.principal_release import KEY_PATH, enrolled  # noqa: E402

if "--confirm" not in sys.argv:
    k = enrolled()
    print(f"enrolled: {k['fingerprint'] if k else 'none'} - rerun with --confirm to retire it")
    sys.exit(0)
k = enrolled()
if not k:
    print("no key is enrolled - nothing to reset")
    sys.exit(0)
d = os.path.join(os.path.dirname(KEY_PATH), "retired")
os.makedirs(d, mode=0o700, exist_ok=True)
dest = os.path.join(d, f"principal_key-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
shutil.move(KEY_PATH, dest)
from core.security_events import emit  # noqa: E402
emit("principal_key_enrolled", agent="operator", resource="principal_key", action="reset", decision="reset",
     reason=f"the enrolled release key {k['fingerprint']} was RESET at the server; a new key may now enrol",
     extra={"fingerprint": k["fingerprint"], "retired_to": os.path.basename(dest)})
print(f"retired {k['fingerprint']} -> {dest}; a new phone can enrol now")
