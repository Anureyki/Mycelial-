#!/usr/bin/env python3
"""A kit declares what it costs only after it has been weighed, and a verb
whose kit is absent says so instead of crashing.

    python3 tools/check_kits.py

No network, no install. The manifest and the absence path are checked; what
a kit weighs on THIS machine is a measurement, not an assertion, and is not
re-run here.
"""
import json
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
    with open(os.path.join(ROOT, "config", "kits.json"), encoding="utf-8") as fh:
        m = json.load(fh)

    print("every kit declares what it is, where it came from, and how to undo it")
    for name, kit in m["kits"].items():
        for field in ("title", "what_it_is_for", "provides", "pip", "verify"):
            ck(f"{name} declares {field}", bool(kit.get(field)))
        src = kit.get("adapted_from") or {}
        ck(f"{name} names the upstream repo and its licence",
           bool(src.get("repo") and src.get("licence")), str(src.get("repo")))
        ck(f"{name} says what we changed, so a fork is not mistaken for a dependency",
           bool(src.get("what_we_changed")))
        ck(f"{name} can be removed: pip_remove or pip is listed",
           bool(kit.get("pip_remove") or kit.get("pip")))
        for rel in kit["provides"]:
            ck(f"{name} provides {rel}, and it exists",
               os.path.exists(os.path.join(ROOT, rel)))

    print("a size is measured or it is not claimed")
    for name, kit in m["kits"].items():
        meas, state = kit.get("measured"), kit.get("state")
        if meas is None:
            ck(f"{name} unmeasured -> state is not 'installed'", state != "installed", str(state))
        else:
            ck(f"{name} measured -> the delta carries a date",
               bool(meas.get("measured_on")) and meas.get("total_mb") is not None, str(meas))

    print("absence is a state with the command attached")
    from tools import fetch_page as F
    ok, why = F.kit_available()
    if ok:
        print("  SKIP  the kit is installed here, so the absence path is checked by shape")
        src = open(os.path.join(ROOT, "tools", "fetch_page.py"), encoding="utf-8").read()
        ck("the absent path reports kit_not_installed, not a traceback",
           "kit_not_installed" in src and "install" in src)
        ck("and names the install command", "tools/kit.py install" in src)
    else:
        ck("absent kit reports kit_not_installed", why and why.get("kit_not_installed"))
        ck("and names the install command", "tools/kit.py install" in str(why.get("install")))

    print("the installer never installs by itself")
    ktext = open(os.path.join(ROOT, "tools", "kit.py"), encoding="utf-8").read()
    ck("no auto-install on a missing kit",
       "auto_install" not in ktext and "def cmd_install" in ktext)
    ck("removal reports what came BACK, not what went in",
       "what came back" in ktext.lower() or "FREED" in ktext)
    ck("install weighs before and after", "_weigh(kit)" in ktext and "before" in ktext)

    print("findings that were reported and not acted on stay visible")
    for f in m.get("_findings", []):
        ck(f"finding {f['id']} carries its state and why", bool(f.get("state")) and
           (f.get("state") != "reported_not_done" or bool(f.get("why_not_done"))))

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
