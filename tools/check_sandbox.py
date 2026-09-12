#!/usr/bin/env python3
"""The sandbox acceptance criteria, as a gate.

    python3 tools/check_sandbox.py

Three claims, each of which was FALSE before this layer existed and each of
which fails the build if it stops holding:

  1. A compromised Trust cannot read Legal's credentials.
  2. A2A messages are signed and provenance-logged.
  3. The narrow channel is the only path in or out.

The third is the one that rots quietest. A verb added to the set for a good
reason is a hole in the boundary, so the set is asserted against a fixed list
here: growing it has to be a deliberate edit in two places.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A GATE'S PROBES MUST NOT REACH THE COLLECTION SPOOL.
#
# The decisions below are REAL on purpose - a denial that was faked would prove
# nothing about the ACL. But a real decision that only happened because a test
# asked for it is not evidence about defending the system, and it was being
# ingested as a usable training pair like any other.
#
# Measured 2026-09-12: 452 of 1,405 records arrived during one night of CI runs
# and the top six scenarios - gate fixtures, verbatim - were 21.3% of the whole
# training set. A model trained there learns the shape of this file's probes.
#
# Two layers, deliberately redundant. MYCELIAL_EVENT_ORIGIN stamps every event
# this process emits as `test`, which pairs() drops; redirecting the spool
# means they never reach the collection path at all. Either alone would do;
# both means a future gate that forgets one is still contained.
import tempfile as _tempfile
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
import core.security_events as _se
_se.SPOOL = _tempfile.mkdtemp(prefix="gate-spool-")


EXPECTED_VERBS = {"fs.read", "fs.write", "fs.list", "proc.exec", "net.fetch",
                  "browser.goto", "browser.read", "browser.click"}
FORBIDDEN = {"shell", "exec", "env.get", "net.post", "fs.delete", "eval"}

fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    # ---- 1. credential isolation ----
    print("\n  1. a compromised agent cannot read another department's credentials")
    from core.sandbox.credentials import env_for, leak_check, load_manifest
    m = load_manifest()
    fake = {k: "x" * 24 for k in m}
    trust = env_for("trust_agent", source=fake, manifest=m)
    legal = env_for("legal_agent", source=fake, manifest=m)
    check("trust cannot see COURTLISTENER_API_TOKEN",
          "COURTLISTENER_API_TOKEN" not in trust)
    check("trust cannot see SOLANA_RPC_URL", "SOLANA_RPC_URL" not in trust)
    check("legal still has its own token",
          "COURTLISTENER_API_TOKEN" in legal)
    check("no cross-department credential visible anywhere",
          not leak_check(m), str(leak_check(m))[:70])
    # SHAPE CHANGED WITH THE MODEL. The manifest used to carry an `owners`
    # LIST because ownership was the permission. Under the ACL a resource has
    # one `owner` plus explicit read/write/execute grants, so asserting the old
    # key here would fail for every correctly-declared resource - which is
    # exactly what CI caught on the migration.
    check("every credential has a named owner",
          all((m[k] or {}).get("owner") for k in m),
          str([k for k in m if not (m[k] or {}).get("owner")])[:60])
    check("permission is a grant, not ownership: read lists exist",
          all(isinstance((m[k] or {}).get("read"), list) for k in m))

    # ---- 2. signed and logged A2A ----
    print("\n  2. A2A artifacts are signed, tamper-evident, and expire")
    import copy
    from core.sandbox.signing import sign, verify, key_permissions_ok
    art = sign("legal_agent", {"amount": 550}, recipient="accounting_agent")
    check("a genuine artifact verifies", verify(art)[0])
    check("a forged sender is refused",
          not verify(art, expected_sender="trust_agent")[0])
    t = copy.deepcopy(art); t["envelope"]["body"]["amount"] = 55000
    check("a tampered body is refused", not verify(t)[0])
    r = copy.deepcopy(art); r["envelope"]["recipient"] = "trust_agent"
    check("a redirected recipient is refused", not verify(r)[0],
          "routing is signed, not just the body")
    from datetime import datetime, timedelta, timezone
    check("an expired artifact is refused",
          not verify(art, now=datetime.now(timezone.utc) + timedelta(hours=2))[0])
    check("private keys are not group/world readable",
          not key_permissions_ok(), str(key_permissions_ok())[:60])

    # ---- 3. the narrow channel ----
    print("\n  3. the narrow channel is the only path, and it is narrow")
    from core.sandbox.channel import Channel, VERBS, ChannelDenied
    from core.sandbox.docker_backend import NullBackend
    check("the verb set is exactly what was agreed",
          set(VERBS) == EXPECTED_VERBS,
          f"extra={sorted(set(VERBS)-EXPECTED_VERBS)} "
          f"missing={sorted(EXPECTED_VERBS-set(VERBS))}")
    check("no shell-shaped verb exists",
          not (set(VERBS) & FORBIDDEN), str(sorted(set(VERBS) & FORBIDDEN)))

    be = NullBackend()
    ch = Channel("trust_agent", be, allow_hosts=("courtlistener.com",),
                 provenance=False)
    for verb, payload, why in [
            ("shell", {"cmd": "ls"}, "an unknown verb"),
            ("proc.exec", {"argv": "rm -rf /"}, "a command STRING not an argv list"),
            ("proc.exec", {"argv": ["curl", "x"]}, "a binary not allowlisted"),
            ("fs.read", {"path": "../../.env"}, "a path escaping the root"),
            ("fs.read", {"path": "/etc/passwd"}, "an absolute path"),
            ("net.fetch", {"url": "https://evil.example/x"}, "a host not allowlisted")]:
        try:
            ch.call(verb, payload)
            check(f"refuses {why}", False, "IT WAS ALLOWED")
        except ChannelDenied:
            check(f"refuses {why}", True)

    ok = ch.call("fs.read", {"path": "notes/x.txt"})
    check("a legitimate call still crosses", ok["outcome"] == "ok")
    check("the backend saw only allowed calls", len(be.calls) == 1,
          f"{len(be.calls)} call(s) reached the backend")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  sandbox boundary holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
