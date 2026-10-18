"""Publish an approved artifact into Anansi's Jellyfin media library.

The principal's media workflow (2026-10-18). Anansi generates; Jellyfin
catalogs and plays. This is the narrow door between them, and every check
runs BEFORE anything is copied:

  1. the source resolves (symlinks followed) INSIDE an authorised output root -
     no "../", no link pointing out of bounds;
  2. the kind is one this library holds, and the file's own bytes say it is
     that kind - a renamed script is not an image;
  3. it is under the size bound for its kind;
  4. the title and description carry no personal identifier (the library is
     viewed on a television; a private record does not belong on it);
  5. it is COPIED, never moved, with 0600 permissions - nothing here can be
     executed, and the original stays where its producer left it.

Jellyfin is told to rescan through its API with a scoped key; a refresh that
fails is reported as failed, never as done.
"""
import hashlib
import json
import os
import re
import shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTHORIZED_SOURCES = [os.path.join(ROOT, "state", "artifacts"), os.path.join(ROOT, "reports")]
MEDIA_ROOT = os.path.join(ROOT, "media", "anansi")

# kind -> (library subfolder, allowed extensions, size cap in bytes)
KINDS = {
    "image":   ("images",   {".png", ".jpg", ".jpeg", ".webp"}, 25 * 1024 ** 2),
    "report":  ("reports",  {".png", ".jpg", ".jpeg", ".webp"}, 25 * 1024 ** 2),
    "diagram": ("diagrams", {".png", ".jpg", ".jpeg", ".webp"}, 25 * 1024 ** 2),
    "video":   ("video",    {".mp4", ".webm", ".mkv"},          300 * 1024 ** 2),
    "audio":   ("audio",    {".mp3", ".ogg", ".flac", ".wav", ".m4a"}, 100 * 1024 ** 2),
}


def _magic_ok(path, ext):
    with open(path, "rb") as fh:
        head = fh.read(16)
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in (".mp4", ".m4a"):
        return head[4:8] == b"ftyp"
    if ext in (".webm", ".mkv"):
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if ext == ".mp3":
        return head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    if ext == ".ogg":
        return head.startswith(b"OggS")
    if ext == ".flac":
        return head.startswith(b"fLaC")
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    return False


def _inside(path, roots):
    real = os.path.realpath(path)
    return any(real == os.path.realpath(r) or real.startswith(os.path.realpath(r) + os.sep)
               for r in roots)


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "untitled")[:60]


def publish(source, kind, title, description="", produced_by=None, refresh=None,
            sources=None, media_root=None, now=None):
    """-> {"published": bool, ...}. `refresh` is a callable returning
    (ok, detail) that asks Jellyfin to rescan; None means not asked."""
    sources = sources or AUTHORIZED_SOURCES
    media_root = media_root or MEDIA_ROOT
    fail = lambda why, **kw: {"published": False, "why": why, **kw}
    if kind not in KINDS:
        return fail(f"kind must be one of {sorted(KINDS)}")
    if not (source and title):
        return fail("needs source and title")
    if not os.path.isfile(source):
        return fail("source is not a file")
    if not _inside(source, sources):
        return fail("source is outside the authorised output locations",
                    authorised=[os.path.relpath(s, ROOT) for s in sources])
    sub, exts, cap = KINDS[kind]
    ext = os.path.splitext(source)[1].lower()
    if ext not in exts:
        return fail(f"{ext or 'no extension'} is not a {kind} type here ({sorted(exts)})")
    size = os.path.getsize(source)
    if size > cap:
        return fail(f"{size:,} bytes exceeds the {cap:,}-byte bound for {kind}")
    if not _magic_ok(source, ext):
        return fail(f"the file's bytes are not a {ext} - refusing a renamed file")
    try:
        from core.identifier_scan import scan
        hits = (scan(f"{title} {description}") or {}).get("findings") or []
    except Exception as exc:
        return fail(f"identifier scan unavailable ({exc}) - not publishing unscanned metadata")
    if hits:
        return fail(f"title/description carry {len(hits)} personal identifier(s) - not for the library")
    with open(source, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    name = f"{stamp}_{_slug(title)}_{digest[:8]}{ext}"
    dest_dir = os.path.join(media_root, sub)
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    if not _inside(dest, [media_root]):
        return fail("destination escaped the media root")
    shutil.copyfile(source, dest)          # copy - never move, never follow into dest
    os.chmod(dest, 0o600)                  # owner-readable (Jellyfin runs as the owner), never executable
    meta = {"title": title, "description": description, "kind": kind,
            "source": os.path.relpath(os.path.realpath(source), ROOT), "sha256": digest,
            "bytes": size, "produced_by": produced_by,
            "published_at": (now or datetime.now()).isoformat(timespec="seconds")}
    with open(dest + ".json", "w") as fh:
        json.dump(meta, fh, indent=2)
    os.chmod(dest + ".json", 0o600)
    out = {"published": True, "path": os.path.relpath(dest, ROOT), "library_folder": sub,
           "metadata": meta}
    if refresh is not None:
        ok, detail = refresh()
        out["library_refresh"] = {"ok": bool(ok), "detail": detail}
    else:
        out["library_refresh"] = {"ok": False, "detail": "not requested"}
    return out
