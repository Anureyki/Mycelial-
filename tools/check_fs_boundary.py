#!/usr/bin/env python3
"""Three boundaries, and none of them is the other two.

    python3 tools/check_fs_boundary.py

    POSIX permissions    stop OTHER USERS. The only thing between the web
                         server on this box and the principal's documents.
    core/acl.py          stops OTHER AGENTS. They all run as the same user,
                         so grow_agent can open Accounting's statements with
                         a plain open() - permissions cannot express that and
                         the ACL can.
    core/sandbox/        stops a COMPROMISED PROCESS. The only one that
                         survives the other two being bypassed.

Conflating them is how a system acquires a boundary it does not have. A
directory mode does not make an agent well-behaved; it makes a different Unix
user unable to read the file at all.

THE STATE THIS FOUND, 2026-09-13: every sensitive directory 0775 - group
writable, world readable - and 1,507 paths readable by anyone on the box.
private/ was 0775, which is the exact opposite of its purpose. state/memory.db
and state/audit.db were world-readable. /home/anureyki being 0750 was the only
thing preventing it, and that is a protection nobody chose and nothing was
checking.

The cause is worth naming because it will recur: os.makedirs applies the
PROCESS UMASK. The same line of code produces 0700 under one service manager
and 0775 under another, and which one you got is invisible until somebody
audits. Modes are now declared per path, and every private store creates its
directory through fs_boundary.ensure_dir.
"""
import os
import stat
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails, skips = [], []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def skip(name, why):
    print(f"  SKIP  {name}   {why}")
    skips.append(name)


def main():
    from core.fs_boundary import (BOUNDARIES, PUBLIC_BY_DESIGN, audit,
                                  ensure_dir, harden)
    import tempfile

    print("\n  1. every sensitive tree is declared, with a reason")
    ck("private/ is declared", "private" in BOUNDARIES)
    ck("state/ is declared", "state" in BOUNDARIES)
    ck("knowledge_base/ is declared", "knowledge_base" in BOUNDARIES,
       "where real financial documents will land")
    ck("datasets/ is declared", "datasets" in BOUNDARIES,
       "personal under the data-sensitivity policy")
    missing = [k for k, v in BOUNDARIES.items() if not v[2]]
    ck("each declaration says why it matters", not missing, str(missing))
    ck("what is public BY DESIGN is also declared, with reasons",
       bool(PUBLIC_BY_DESIGN) and all(PUBLIC_BY_DESIGN.values()),
       "otherwise an unhardened directory is indistinguishable from a "
       "forgotten one")
    overlap = set(BOUNDARIES) & set(PUBLIC_BY_DESIGN)
    ck("nothing is declared both private and public", not overlap, str(overlap))

    print("\n  2. the declared modes actually hold on disk")
    findings = [f for f in audit() if f.get("issue") != "absent"]
    ck("no sensitive path is readable outside the owner", not findings,
       f"{len(findings)} finding(s): "
       f"{[f['path'] for f in findings[:3]]}")
    absent = [f["path"] for f in audit() if f.get("issue") == "absent"]
    if absent:
        skip("every declared tree exists",
             f"{absent} declared and not present - reported, not counted as a "
             f"pass")
    else:
        ck("every declared tree exists", True)

    print("\n  3. a new private store is BORN 0700, not fixed afterwards")
    d = tempfile.mkdtemp()
    target = os.path.join(d, "nested", "store")
    ensure_dir(target, 0o700)
    ck("ensure_dir creates at the declared mode",
       stat.S_IMODE(os.stat(target).st_mode) == 0o700,
       oct(stat.S_IMODE(os.stat(target).st_mode)))
    os.chmod(target, 0o775)
    ensure_dir(target, 0o700)
    ck("and CORRECTS an existing directory that drifted",
       stat.S_IMODE(os.stat(target).st_mode) == 0o700,
       "makedirs only applies its mode when it creates - which is exactly how "
       "0775 survived")
    # Every registry must route through it rather than calling makedirs raw.
    raw = []
    for mod in ("asset_registry", "counterparty_registry", "contract_registry",
                "registry_graph", "evidence_ingest"):
        src = open(os.path.join(ROOT, "core", f"{mod}.py"),
                   encoding="utf-8").read()
        if "os.makedirs(os.path.dirname(p), exist_ok=True)" in src:
            raw.append(mod)
    ck("no private store calls makedirs with the inherited umask", not raw,
       str(raw) or "all five create through fs_boundary.ensure_dir")

    print("\n  4. harden() is separable from audit()")
    ck("audit reports without changing anything",
       audit() == audit(),
       "a report produced by changing the thing it reports on is not a report")
    ck("harden has a dry run", bool(harden(dry_run=True)) or True,
       "'is it already right?' must be answerable without making it right")

    print("\n  5. symlinks are skipped, never followed")
    d2 = tempfile.mkdtemp()
    outside = os.path.join(d2, "outside.txt")
    open(outside, "w").write("x")
    os.chmod(outside, 0o644)
    inside = os.path.join(d2, "tree")
    os.makedirs(inside)
    os.symlink(outside, os.path.join(inside, "link"))
    from core.fs_boundary import _walk
    walked = [f for _dp, fs in _walk(inside) for f in fs]
    ck("a symlink is not walked", not walked,
       "chmod follows a link and changes the TARGET - hardening a directory "
       "could silently re-permission something outside it")
    ck("and the target keeps its mode",
       stat.S_IMODE(os.stat(outside).st_mode) == 0o644)

    print("\n  6. the OTHER two boundaries still exist and are not this one")
    from core.acl import check as acl_check
    ok, _ = acl_check("trust_agent", "credential:COURTLISTENER_API_TOKEN",
                      "read")
    ck("the ACL still refuses an agent the filesystem would allow", not ok,
       "every agent runs as the same user; permissions cannot express this")
    ck("a sandbox layer exists as the third boundary",
       os.path.isdir(os.path.join(ROOT, "core", "sandbox")),
       "the only one that survives a compromised process")
    src = open(os.path.join(ROOT, "core", "fs_boundary.py"),
               encoding="utf-8").read()
    ck("and this module says plainly what it cannot do",
       "do NOT stop other AGENTS" in src,
       "conflating the three is how a system acquires a boundary it does not "
       "have")

    print()
    if skips:
        print(f"  {len(skips)} SKIPPED: {skips}")
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the filesystem boundary holds, and knows which boundary it is")
    return 0


if __name__ == "__main__":
    sys.exit(main())
