#!/usr/bin/env python3
"""Capability kits: download a tool when it is needed, give the disk back after.

    python3 tools/kit.py list
    python3 tools/kit.py status [name]
    python3 tools/kit.py install <name>
    python3 tools/kit.py remove <name>
    python3 tools/kit.py space

THE PROBLEM THIS SOLVES, in the principal's words: "tools don't just take up
space like this. So when you need it, you can download the kit." This
machine is 54G and sat at 91% full while a rendering tool that runs a few
times a week held 1.3G of browser and 250M of Python.

WHAT A KIT IS. The ADAPTER is ours and lives in this repo - small, edited,
reviewed, and it is what `provides` names. The DEPENDENCY TREE is not
vendored: it is fetched on install and removed on uninstall. So the repo
carries the capability and the disk carries it only while it is wanted.
That is also why `adapted_from` records the upstream repo, its licence and
the path taken: a fork of one path with our edits is a different object
from a dependency, and the difference is worth stating in the manifest
rather than in somebody's memory.

MEASURED, NOT ESTIMATED. Install weighs the venv and the declared cache
directories before and after and writes the delta into config/kits.json.
A kit that reports a size nobody weighed it at is the false-success shape
this project exists to catch, so the field starts null and the state
starts `unmeasured`.

REMOVAL IS VERIFIED THE SAME WAY. Uninstall weighs again and reports what
actually came back, which is not always what went in - pip leaves shared
dependencies alone when something else needs them, and saying "freed 1.3G"
when 200M returned is a lie a tidy-up script tells easily.

WHAT IT WILL NOT DO. It never installs by itself. A verb that finds its
kit missing reports `kit_not_installed` and the command; it does not
helpfully download a gigabyte because somebody asked a question. Absent
and unreachable are different findings and this keeps them apart.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "config", "kits.json")
VENV = os.path.join(ROOT, "venv")
SITE = os.path.join(VENV, "lib")


def _load():
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def _save(m):
    fd = os.open(MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _du(path):
    """Bytes under a path, 0 if absent. du, because a walk on a venv is slow."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return 0
    try:
        out = subprocess.run(["du", "-sb", path], capture_output=True, text=True,
                             timeout=300)
        return int(out.stdout.split()[0]) if out.returncode == 0 else 0
    except Exception:                               # noqa: BLE001
        return 0


def _weigh(kit):
    """-> {venv, caches, total} in bytes, right now."""
    venv = _du(SITE)
    caches = {d: _du(d) for d in (kit.get("cache_dirs") or [])}
    return {"venv_lib": venv, "caches": caches, "total": venv + sum(caches.values())}


def _mb(n):
    return round(n / (1024 * 1024), 1)


def _installed(kit):
    """Is the adapter's import actually satisfiable? Read the venv, do not
    trust a flag in the manifest - a flag is a claim and the venv is a fact."""
    pkgs = [p.split("==")[0].replace("-", "_") for p in (kit.get("pip") or [])]
    if not pkgs:
        return True
    probe = ("import importlib.util,sys;"
             "sys.exit(0 if all(importlib.util.find_spec(m) for m in "
             + repr(pkgs) + ") else 1)")
    py = os.path.join(VENV, "bin", "python3")
    if not os.path.exists(py):
        return False
    try:
        return subprocess.run([py, "-c", probe], capture_output=True,
                              timeout=120).returncode == 0
    except Exception:                               # noqa: BLE001
        return False


def disk():
    total, used, free = shutil.disk_usage("/")
    return {"total_gb": round(total / 1e9, 1), "free_gb": round(free / 1e9, 1),
            "used_pct": round(used / total * 100, 1)}


def cmd_list(_a):
    m = _load()
    d = disk()
    print(f"disk: {d['free_gb']} GB free of {d['total_gb']} GB ({d['used_pct']}% used)")
    print()
    for name, kit in m["kits"].items():
        on = _installed(kit)
        meas = kit.get("measured") or {}
        size = (f"{meas.get('total_mb')} MB measured" if meas.get("total_mb")
                else "size not measured yet")
        print(f"  {'[installed]' if on else '[  absent  ]'} {name:<14} {size}")
        print(f"                 {kit['title']} - {kit['what_it_is_for'][:88]}")
        src = kit.get("adapted_from") or {}
        if src:
            print(f"                 adapted from {src.get('repo')} ({src.get('licence')})")
    for f in m.get("_findings", []):
        if f.get("state") == "reported_not_done":
            print(f"\n  FINDING (not acted on): {f['what'][:100]}")
            print(f"    cost: {f['cost']}")
            print(f"    remedy: {f['remedy']}")
    return 0


def cmd_status(a):
    m = _load()
    names = [a.name] if a.name else list(m["kits"])
    for n in names:
        kit = m["kits"].get(n)
        if not kit:
            print(f"  no kit named {n!r}")
            continue
        print(json.dumps({"kit": n, "installed": _installed(kit),
                          "measured": kit.get("measured"), "state": kit.get("state"),
                          "provides": kit.get("provides"),
                          "install": f"python3 tools/kit.py install {n}"}, indent=2))
    return 0


def cmd_install(a):
    m = _load()
    kit = m["kits"].get(a.name)
    if not kit:
        print(f"no kit named {a.name!r}; known: {sorted(m['kits'])}")
        return 1
    if _installed(kit) and not a.force:
        print(f"{a.name} is already installed. `remove` first to re-measure.")
        return 0
    before = _weigh(kit)
    d = disk()
    print(f"disk before: {d['free_gb']} GB free ({d['used_pct']}% used)")
    py = os.path.join(VENV, "bin", "python3")
    for spec in kit.get("pip") or []:
        print(f"  pip install {spec}")
        rc = subprocess.call([py, "-m", "pip", "install", spec])
        if rc != 0:
            print(f"  FAILED: pip exited {rc}. Nothing measured; kit left as it is.")
            return rc
    for step in kit.get("post_install") or []:
        cmd = [py if step[0] == "python" else step[0]] + step[1:]
        print(f"  {' '.join(cmd)}")
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"  FAILED: post-install exited {rc}.")
            return rc
    after = _weigh(kit)
    delta = {"venv_lib_mb": _mb(after["venv_lib"] - before["venv_lib"]),
             "caches_mb": {k: _mb(after["caches"].get(k, 0) - before["caches"].get(k, 0))
                           for k in after["caches"]},
             "total_mb": _mb(after["total"] - before["total"]),
             "measured_on": subprocess.run(["date", "-I"], capture_output=True,
                                           text=True).stdout.strip()}
    kit["measured"] = delta
    kit["state"] = "installed"
    _save(m)
    d = disk()
    print(f"\n  MEASURED: {delta['total_mb']} MB "
          f"(venv {delta['venv_lib_mb']} MB, caches {delta['caches_mb']})")
    print(f"  disk after: {d['free_gb']} GB free ({d['used_pct']}% used)")
    if kit.get("verify"):
        print(f"  verify with: {' '.join(kit['verify'])}")
    return 0


def cmd_remove(a):
    m = _load()
    kit = m["kits"].get(a.name)
    if not kit:
        print(f"no kit named {a.name!r}")
        return 1
    before = _weigh(kit)
    py = os.path.join(VENV, "bin", "python3")
    pkgs = kit.get("pip_remove") or [p.split("==")[0] for p in (kit.get("pip") or [])]
    if pkgs:
        print(f"  pip uninstall -y {len(pkgs)} package(s)")
        subprocess.call([py, "-m", "pip", "uninstall", "-y"] + pkgs)
    for d in kit.get("cache_dirs") or []:
        p = os.path.expanduser(d)
        if os.path.isdir(p):
            print(f"  removing cache {p}")
            shutil.rmtree(p, ignore_errors=True)
    after = _weigh(kit)
    freed = {"venv_lib_mb": _mb(before["venv_lib"] - after["venv_lib"]),
             "caches_mb": {k: _mb(before["caches"].get(k, 0) - after["caches"].get(k, 0))
                           for k in before["caches"]},
             "total_mb": _mb(before["total"] - after["total"])}
    kit["state"] = "removed"
    kit["last_removal_freed"] = freed
    _save(m)
    d = disk()
    # WHAT CAME BACK, not what went in. pip leaves a shared dependency alone
    # when something else still needs it, so the two numbers differ and the
    # honest one is the one weighed after.
    claimed = (kit.get("measured") or {}).get("total_mb")
    print(f"\n  FREED: {freed['total_mb']} MB"
          + (f" (install measured {claimed} MB; the difference is shared "
             f"dependencies other things still need)" if claimed else ""))
    print(f"  disk now: {d['free_gb']} GB free ({d['used_pct']}% used)")
    return 0


def cmd_space(_a):
    m = _load()
    d = disk()
    print(f"disk: {d['free_gb']} GB free of {d['total_gb']} GB ({d['used_pct']}% used)")
    print(f"venv site-packages: {_mb(_du(SITE))} MB")
    rows = []
    sp = None
    for base, dirs, _files in os.walk(SITE):
        if os.path.basename(base) == "site-packages":
            sp = base
            break
    if sp:
        for entry in os.listdir(sp):
            p = os.path.join(sp, entry)
            if os.path.isdir(p):
                rows.append((_du(p), entry))
        rows.sort(reverse=True)
        print("\nlargest packages:")
        for size, name in rows[:12]:
            print(f"  {_mb(size):>9} MB  {name}")
    print("\nkit caches:")
    for name, kit in m["kits"].items():
        for cd in kit.get("cache_dirs") or []:
            print(f"  {_mb(_du(cd)):>9} MB  {cd}  ({name})")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("status")
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("install")
    s.add_argument("name")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_install)
    s = sub.add_parser("remove")
    s.add_argument("name")
    s.set_defaults(fn=cmd_remove)
    sub.add_parser("space").set_defaults(fn=cmd_space)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
