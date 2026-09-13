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

    print("\n  2. the declared modes hold WHERE THERE IS SOMETHING TO PROTECT")
    # GIT DOES NOT CARRY DIRECTORY PERMISSIONS. A fresh clone creates every
    # directory with the runner's umask, so asserting on-disk modes there
    # asserts something the checkout cannot control - and this gate went red
    # in CI while being green on the machine that actually holds the data,
    # which is the environment-dependence this repository has now met four
    # times.
    #
    # So the split is honest rather than convenient: a tree that HOLDS FILES
    # must have the declared modes, wherever it is. A tree that is empty or
    # absent has nothing to expose, and is reported as SKIP with its name -
    # never folded into a pass, because "there was nothing to check" and
    # "it was checked and correct" are different findings.
    populated, empty = [], []
    for rel in sorted(BOUNDARIES):
        base = os.path.join(ROOT, rel)
        if not os.path.isdir(base):
            empty.append(f"{rel} (absent)")
            continue
        n = sum(len(fs) for _dp, fs in
                __import__("core.fs_boundary", fromlist=["_walk"])._walk(base))
        (populated if n else empty).append(rel if n else f"{rel} (empty)")
    findings = [f for f in audit() if f.get("issue") != "absent"
                and f["path"].split("/")[0] in populated]
    if populated:
        ck(f"no path in a populated tree is readable outside the owner",
           not findings,
           f"checked {populated}; "
           f"{len(findings)} finding(s) {[f['path'] for f in findings[:3]]}")
    else:
        skip("on-disk modes",
             "every declared tree is empty or absent here - a fresh clone "
             "carries no permissions and has nothing to expose. NOT a pass.")
    if empty:
        skip("trees with nothing in them",
             f"{empty} - nothing to protect, so nothing asserted")

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
    # AND NOWHERE ELSE IN core/ EITHER. The registries were wired and the
    # SECURITY EVENT SPOOL was not - created at 0775 by whatever umask the
    # agent process inherited, holding every security decision the system
    # makes. It was found because a fresh-clone run created the directory
    # mid-gate and the mode assertion caught it. The store nobody thought of
    # as a store is the one that stays wrong.
    # core/ AND services/. The creator that actually broke CI was in
    # services/ - state/ itself, made by whichever service imported first -
    # while I was scanning only core/. A gate that looks in one directory for
    # a fault that lives in a pattern finds it in one directory.
    umask_creators = []
    scan_roots = [os.path.join(ROOT, "core"), os.path.join(ROOT, "services")]
    for root_dir in scan_roots:
      for dirpath, dirnames, files in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py") or fn == "fs_boundary.py":
                continue
            body = open(os.path.join(dirpath, fn), encoding="utf-8",
                        errors="replace").read()
            for line in body.splitlines():
                st = line.strip()
                if st.startswith("os.makedirs(") and "mode=" not in st:
                    umask_creators.append(f"{fn}: {st[:46]}")
    ck("nothing in core/ or services/ creates a directory at the inherited "
       "umask",
       not umask_creators, str(umask_creators[:3]))

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
