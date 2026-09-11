#!/usr/bin/env python3
"""Fail when the webapp shell changed without a cache-version bump.

The service worker caches by URL. `index.html` requests `app.js?v=N`, so an
edit to app.js that leaves N alone produces the SAME url - and an already
installed client keeps serving the copy it has. On a phone launched from the
home screen there is no hard-refresh gesture to escape it, so the change is
invisible to the one person who needed to see it while looking completely
deployed from this side.

That happened: the Grow and Progress cards were rewritten, shipped, and the
grower's dashboard kept rendering the old narrated paragraphs. Nothing was
wrong with the code or the server. The URL had not changed.

So the fingerprint of every shell file is recorded next to the version, and
this fails if a file moved while the version did not. Run in CI, where a check
that does not get tired outranks remembering.
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "webapp", "shell.manifest.json")


def main():
    if not os.path.exists(MANIFEST):
        print("no webapp/shell.manifest.json - nothing to check")
        return 0
    man = json.load(open(MANIFEST))

    # THE TWO VERSION MARKERS MUST AGREE WITH EACH OTHER.
    #
    # The service worker's CACHE name and the ?v= on index.html's script tag are
    # separate strings that have to be bumped together, and nothing compared
    # them. On 2026-09-06 the worker went to v26 while index.html stayed at
    # v25 - the shell was bumped twice and the script tag once - so the cache
    # was invalidated under a name that no longer matched the URL being asked
    # for. Both files "looked bumped" in a diff.
    import re as _re
    try:
        _sw = open(os.path.join(ROOT, "webapp", "service-worker.js")).read()
        _ix = open(os.path.join(ROOT, "webapp", "index.html")).read()
        _swv = _re.search(r"mycelial-shell-v(\d+)", _sw)
        _ixv = _re.search(r"app\.js\?v=(\d+)", _ix)
        if _swv and _ixv and _swv.group(1) != _ixv.group(1):
            print(f"SHELL VERSION MARKERS DISAGREE: service-worker says "
                  f"v{_swv.group(1)}, index.html asks for app.js?v={_ixv.group(1)}.")
            print("They are bumped together or the cache is invalidated under a name")
            print("that does not match the URL the page requests.")
            return 1
    except Exception as _e:
        print(f"could not compare version markers: {_e}")
        return 1

    drifted = []
    for rel, recorded in man["files"].items():
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            drifted.append((rel, "missing"))
            continue
        actual = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
        if actual != recorded:
            drifted.append((rel, actual))
    if not drifted:
        print(f"shell v{man['version']} - all {len(man['files'])} files match")
        return 0
    print(f"SHELL CHANGED WITHOUT A CACHE BUMP (still v{man['version']}):")
    for rel, actual in drifted:
        print(f"  {rel}  recorded={man['files'][rel]}  actual={actual}")
    print("\nAn installed client will keep serving the OLD file, because the URL")
    print("index.html asks for did not change. Bump the version in")
    print("webapp/service-worker.js and webapp/index.html, then regenerate:")
    print("  python3 tools/check_shell_version.py --update")
    return 1


if __name__ == "__main__":
    if "--update" in sys.argv:
        import re
        sw = open(os.path.join(ROOT, "webapp", "service-worker.js")).read()
        v = int(re.search(r"mycelial-shell-v(\d+)", sw).group(1))
        files = {}
        for rel in ("webapp/index.html", "webapp/app.js", "webapp/style.css",
                    "webapp/service-worker.js"):
            files[rel] = hashlib.sha256(
                open(os.path.join(ROOT, rel), "rb").read()).hexdigest()[:16]
        json.dump({"version": v, "files": files}, open(MANIFEST, "w"), indent=2)
        print(f"recorded shell v{v}")
        sys.exit(0)
    sys.exit(main())
