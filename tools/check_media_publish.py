#!/usr/bin/env python3
"""Only approved artifacts reach the media library, and nothing there runs.

    python3 tools/check_media_publish.py      (temporary directories only)
"""
import os
import stat
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    import core.media_publish as M
    tmp = tempfile.mkdtemp()
    src, lib, outside = (os.path.join(tmp, d) for d in ("artifacts", "media", "outside"))
    for d in (src, lib, outside):
        os.makedirs(d)
    good = os.path.join(src, "chart.png")
    open(good, "wb").write(PNG)
    open(os.path.join(outside, "secret.png"), "wb").write(PNG)
    os.symlink(os.path.join(outside, "secret.png"), os.path.join(src, "link.png"))
    open(os.path.join(src, "evil.png"), "wb").write(b"#!/bin/sh\nrm -rf ~\n")
    pub = lambda s, k="image", t="GSC1 chart", **kw: M.publish(s, k, t, sources=[src], media_root=lib, **kw)

    ck("a path climbing out with ../ is refused",
       not pub(os.path.join(src, "..", "outside", "secret.png"))["published"])
    ck("a symlink pointing out of bounds is refused", not pub(os.path.join(src, "link.png"))["published"])
    r = pub(os.path.join(src, "evil.png"))
    ck("a shell script renamed .png is refused by its bytes", not r["published"] and "bytes" in r["why"], r.get("why"))
    ck("an image is not accepted as a video", not pub(good, k="video")["published"])
    ck("an unknown kind is refused", not pub(good, k="executable")["published"])
    ck("an identifier in the title is refused", not pub(good, t="Report for SSN " + "123-45-" + "6789")["published"])
    big = os.path.join(src, "big.png")
    with open(big, "wb") as fh:
        fh.write(PNG)
        fh.truncate(26 * 1024 ** 2)
    ck("an image over the size bound is refused", not pub(big)["published"])

    r = pub(good, refresh=lambda: (False, "HTTP 401"))
    ck("a good image publishes", r["published"], r.get("why"))
    dest = os.path.join(ROOT, r["path"]) if r.get("published") else ""
    if r.get("published"):
        mode = os.stat(dest).st_mode
        ck("it lands in the images folder with a dated name", r["library_folder"] == "images"
           and os.path.basename(dest)[:4].isdigit())
        ck("it is not executable", not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH), oct(mode))
        ck("the original is kept (copied, not moved)", os.path.exists(good))
        ck("metadata sidecar written", os.path.exists(dest + ".json"))
    ck("a failed library refresh is reported as failed", r.get("library_refresh", {}).get("ok") is False)
    if fails:
        print(f"\nFAIL: {len(fails)} check(s): {fails}")
        return 1
    print("\nOK - only approved artifacts reach the library, and nothing there runs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
