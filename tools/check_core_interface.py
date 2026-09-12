#!/usr/bin/env python3
"""The OS may ask Core. It may never be able to obey Core.

    python3 tools/check_core_interface.py

WHAT IS BEING PROTECTED. Core is a trained model whose promoted checkpoint
agrees with the ACL on 80.9% of real permission questions and gets only 34.7%
of the ALLOWS right - measured by tools/core_agreement.py. A model like that
wired into a gate would refuse legitimate work two times in three. So the
interface is advisory BY CONSTRUCTION, and this asserts the construction
rather than trusting the intention.

IT RUNS WITHOUT CORE. CI has no model, no torch and no serving checkpoint, so
every assertion here is either static or exercises the UNAVAILABLE path - which
is the path that matters most anyway, because it is the one that runs whenever
Core is down.

THE FOUR THINGS THAT MUST STAY TRUE:

  no boolean        a truthy return is one refactor from `if ask(...):`
                    guarding a resource
  three states      available / unavailable are not two - unavailable is
                    neither allow nor deny, and a caller must handle it
  nothing enforces  no code path in the OS may turn an opinion into access
  no blank fields   a missing field must be refused, never rendered into
                    "agent=None" and answered confidently
"""
import ast
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    from core import core_client

    print("\n  1. unavailable is a real state, and it is not a decision")
    # Point at a port nothing listens on: this is the Core-is-down path.
    core_client.CORE_URL = "http://127.0.0.1:9"
    r = core_client.ask("trust_agent", "read", "credential:X", timeout=1)
    ck("an unreachable Core returns available=false",
       r.get("available") is False, str(r.get("why"))[:60])
    ck("it carries a reason", bool(r.get("why")))
    ck("it returns NO decision at all",
       "decision" not in r and "opinion" not in r,
       "Core being down must not read as allow OR as deny")
    ck("a blank field is refused before any request is made",
       core_client.ask("", "read", "x").get("available") is False)
    m = core_client.model()
    ck("model() is unavailable too, not a default", m.get("available") is False)

    print("\n  2. the reply cannot be mistaken for permission")
    src = open(os.path.join(ROOT, "core", "core_client.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    bare = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, bool):
            bare.append(node.lineno)
    ck("no function returns a bare True/False", not bare, str(bare))
    ck("ask() returns a mapping", isinstance(r, dict))
    ck("the opinion is nested, so a caller must reach for it",
       "opinion" in src and "authority" in src,
       "an opinion at the top level invites `if ask(...)`")

    print("\n  3. nothing in the OS enforces a model's opinion")
    # A call to the client whose result reaches a permission decision would be
    # the whole failure. Nothing may import it except tooling and, later, a
    # surface that DISPLAYS it.
    ALLOWED_IMPORTERS = {"tools/check_core_interface.py",
                         "tools/core_agreement.py"}
    importers = []
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "venv", "__pycache__", "quarantine",
                                    "state", "node_modules"}
                       and not d.startswith("backup_")]
        for f in files:
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), ROOT)
            if rel in ALLOWED_IMPORTERS or rel == "core/core_client.py":
                continue
            body = open(os.path.join(dirpath, f), encoding="utf-8",
                        errors="replace").read()
            if "core_client" in body:
                importers.append(rel)
    ck("core_client is imported only by tooling", not importers,
       str(importers) or "acl.py and the guard path must never consult a model")

    acl = open(os.path.join(ROOT, "core", "acl.py"), encoding="utf-8").read()
    ck("the ACL does not consult Core",
       "core_client" not in acl and "8018" not in acl,
       "the interior fails closed on its OWN authority and waits on nothing")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the interface advises and cannot decide")
    return 0


if __name__ == "__main__":
    sys.exit(main())
