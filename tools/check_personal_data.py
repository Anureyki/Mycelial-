#!/usr/bin/env python3
"""Personal-data firewall - a Mycos gate that judges CONTENT, not commands.

    python3 tools/check_personal_data.py --staged          # pre-commit: what is about to be committed
    python3 tools/check_personal_data.py                    # CI: every tracked file
    python3 tools/check_personal_data.py --refresh-derived  # rebuild the local digests from Hermes
    python3 tools/check_personal_data.py --promote          # copy logged near-miss shapes into the config

The permission system judges commands; it blocked a combined commit-and-push on
2026-11-03 and that forced a scan which found the principal's birth time and
longitude in a docstring. This gate is the scan, made permanent.

Three rules, the principal's:
  1. Patterns live in config/personal_data_patterns.json. Nothing here knows a
     category; a new one is a config entry with synthetic fixtures.
  2. Fail closed. A missing or unreadable config, an invalid pattern, a failing
     self-test, a scanner exception, or (locally) a missing digest file BLOCKS.
  3. Every catch is logged to state/personal_data/catches.jsonl with file, line,
     category and reason, plus a MASKED shape of what matched (never the value).
     Each logged shape becomes a regression case the patterns must keep matching;
     --promote copies them into the config so they survive this machine.

Alongside the patterns, the principal's real values (from the natal record) are
checked as keyed HMAC digests - the gate can recognise them without holding them.
Alongside check_no_secrets, not instead of it: that one owns credentials and
card numbers; this one owns the person.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "personal_data_patterns.json")
STATE = os.path.join(ROOT, "state", "personal_data")
CATCHES = os.path.join(STATE, "catches.jsonl")
STEMS, BRANCHES = "甲乙丙丁戊己庚辛壬癸", "子丑寅卯辰巳午未申酉戌亥"
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"), 1)}
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".heic", ".ico", ".woff", ".woff2", ".ttf",
            ".zip", ".gz", ".db", ".sqlite", ".pyc", ".whl", ".so")


class Blocked(Exception):
    pass


def load_config(path=CONFIG):
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Blocked(f"pattern config unreadable ({exc}) - the scan cannot run, so the commit is blocked")
    cats = cfg.get("categories")
    if not isinstance(cats, dict) or not cats:
        raise Blocked("pattern config has no categories - nothing to scan with is not a pass")
    compiled = {}
    for name, c in cats.items():
        try:
            flags = re.I if "i" in (c.get("flags") or "") else 0
            compiled[name] = (re.compile(c["pattern"], flags), c)
        except (re.error, KeyError) as exc:
            raise Blocked(f"category {name!r} has an invalid pattern ({exc})")
    return cfg, compiled


def mask(text, how):
    """The shape of a catch, without the value. Each mask keeps exactly what the
    category's pattern needs to match again."""
    if how == "token":
        cut = text.find("_", text.find("_") + 1) + 1 or 4
        return text[:cut] + re.sub(r"[A-Za-z0-9]", "x", text[cut:])
    if how == "pillars":
        return "".join("甲" if ch in STEMS else "子" if ch in BRANCHES else ch for ch in text)
    out = re.sub(r"\d", "0", text)
    if how == "words":
        out = re.sub(r"\b[A-Z][a-z]+\b", lambda m: "X" + "x" * (len(m.group()) - 1), out)
    return out


def self_test(cfg, compiled):
    """Synthetic fixtures, then every learned case. A pattern that no longer
    catches what it once caught is a regression and blocks."""
    fails = []
    for name, (rx, c) in compiled.items():
        for f in c.get("fixtures_hit") or []:
            if not rx.search(f):
                fails.append(f"{name}: misses its own fixture {f[:60]!r}")
        for f in c.get("fixtures_miss") or []:
            if rx.search(f):
                fails.append(f"{name}: fires on a fixture it must miss {f[:60]!r}")
        if not c.get("fixtures_hit"):
            fails.append(f"{name}: has no fixture proving it detects anything")
    learned = list(cfg.get("learned_cases") or [])
    try:
        with open(CATCHES, encoding="utf-8") as fh:
            learned += [json.loads(x) for x in fh if x.strip()]
    except OSError:
        pass
    for case in learned:
        cat, shape = case.get("category"), case.get("shape")
        if cat in compiled and shape and not compiled[cat][0].search(shape):
            fails.append(f"{cat}: no longer catches a remembered near-miss {shape[:60]!r}")
    # the derived layer, proved on a synthetic key and values so it is tested in CI too
    k = b"synthetic-key-for-self-test"
    digests = {_digest(k, t) for t in ("date:1990-06-15", "coord:-104.99", "chart:庚午壬午丙寅乙未")}
    probe = 'born 1990-06-15 at lon -104.9903 chart 庚午 壬午 丙寅 乙未'
    hits = {t.split(":")[0] for t in _tokens(probe) if _digest(k, t) in digests}
    if hits != {"date", "coord", "chart"}:
        fails.append(f"derived layer: synthetic values not all recognised ({sorted(hits)})")
    if any(_digest(k, t) in digests for t in _tokens("2024-02-10 lon 121.47 甲辰 丙寅 甲辰 庚午")):
        fails.append("derived layer: matched values that are not on the record")
    # masks must not carry the value they mask
    if "1990" in mask("1990-06-15", "digits") or "abcd" in mask("github_pat_11abcdEFGH0000000000000000", "token"):
        fails.append("mask leaks the value it masks")
    return fails, len(learned)


# ---------------------------------------------------------------- derived layer
def _key():
    p = os.path.join(ROOT, "state", "personal_data", "hmac.key")
    with open(p, "rb") as fh:
        return fh.read()


def _digest(key, token):
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def _tokens(text):
    """Candidate values in a text, normalised the way the derived digests are."""
    for m in re.finditer(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})", text):
        yield f"date:{m.group(1)}-{m.group(2)}-{m.group(3)}"
    for m in re.finditer(r"\b([A-Za-z]{3,9})\.? (\d{1,2}), ((?:19|20)\d{2})\b", text):
        mon = MONTHS.get(m.group(1).lower()) or next(
            (v for k, v in MONTHS.items() if k.startswith(m.group(1).lower()[:3])), None)
        if mon:
            yield f"date:{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    for m in re.finditer(r"(?<![\d.])(-?\d{1,3}\.\d{2,})", text):
        v = float(m.group(1))
        yield f"coord:{v:.2f}"
    for m in re.finditer(r"(?:[%s][%s][\s,'\"·/|\-]{0,4}){4}" % (STEMS, BRANCHES), text):
        yield "chart:" + "".join(ch for ch in m.group() if ch in STEMS + BRANCHES)


def refresh_derived(cfg):
    """Read the live natal record and keep only keyed digests of its values."""
    import urllib.request
    sys.path.insert(0, ROOT)
    from ati.store import HermesStore

    class Shim:
        def send_a2a(self, agent, task, args):
            req = urllib.request.Request("http://localhost:8002/execute", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"task": task, "args": args, "sender": "anansi"}).encode())
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
    d = cfg.get("derived") or {}
    rec = HermesStore(Shim()).get_natal(d.get("entity_id") or "principal")
    if not rec:
        raise Blocked("no natal record readable from Hermes - nothing to derive digests from")
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    kp = os.path.join(ROOT, d.get("key_file") or "state/personal_data/hmac.key")
    if not os.path.exists(kp):
        fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(secrets.token_bytes(32))
    key = _key()
    vals = set()
    local = str(rec.get("birth_datetime_local") or "")
    if len(local) >= 10:
        vals.add(f"date:{local[:10]}")
    loc = rec.get("birth_location") or {}
    for k in ("lat", "lon"):
        if isinstance(loc, dict) and isinstance(loc.get(k), (int, float)):
            vals.add(f"coord:{float(loc[k]):.2f}")
    bp = rec.get("bazi_pillars") or {}
    if isinstance(bp, dict) and all(isinstance(bp.get(k), dict) for k in ("year", "month", "day", "hour")):
        vals.add("chart:" + "".join(bp[k]["hanzi"] for k in ("year", "month", "day", "hour")))
    out = {"built_at": datetime.now().isoformat(timespec="seconds"), "source": "Hermes ati natal record",
           "kinds": sorted({v.split(":")[0] for v in vals}), "digests": sorted(_digest(key, v) for v in vals)}
    dp = os.path.join(ROOT, d.get("digest_file") or "state/personal_data/derived.json")
    fd = os.open(dp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh)
    return out


def load_derived(cfg):
    d = cfg.get("derived") or {}
    dp = os.path.join(ROOT, d.get("digest_file") or "state/personal_data/derived.json")
    with open(dp, encoding="utf-8") as fh:
        return set(json.load(fh)["digests"]), _key()


# ---------------------------------------------------------------- scanning
def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def read(path, staged):
    if staged:
        r = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True, check=True)
        return r.stdout.decode("utf-8", errors="replace")
    with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as fh:
        return fh.read()


_NS_ALLOWED = None


def _no_secrets_allowed():
    """check_no_secrets' reviewed allowlist, read from its source (by AST, not a copy)."""
    global _NS_ALLOWED
    if _NS_ALLOWED is None:
        import ast
        tree = ast.parse(open(os.path.join(ROOT, "tools", "check_no_secrets.py"), encoding="utf-8").read())
        _NS_ALLOWED = next((set(ast.literal_eval(n.value).keys()) for n in tree.body
                            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "ALLOWED" for t in n.targets)),
                           None)
        if _NS_ALLOWED is None:
            raise Blocked("check_no_secrets.ALLOWED could not be read - the scoped allowance cannot be applied")
    return _NS_ALLOWED


def allowed(cfg, path, line, category=None):
    """An allowance names a path (optionally a snippet), a path prefix, or another
    gate's reviewed list - and, when it says so, only some categories."""
    for a in cfg.get("allowlist") or []:
        cats = a.get("categories")
        if cats and category not in cats:
            continue
        if a.get("path") == path and (not a.get("contains") or a["contains"] in line):
            return a.get("why")
        if a.get("path_prefix") and path.startswith(a["path_prefix"]):
            return a.get("why")
        if a.get("source") == "tools/check_no_secrets.py ALLOWED" and path in _no_secrets_allowed():
            return a.get("why")
    return None


def scan(cfg, compiled, files, staged, derived=None):
    catches = []
    for path in files:
        if path.endswith(SKIP_EXT) or path.startswith(("state/", "knowledge_base/")):
            continue
        text = read(path, staged)
        for n, line in enumerate(text.splitlines(), 1):
            for name, (rx, c) in compiled.items():
                for m in rx.finditer(line):
                    if allowed(cfg, path, line, name):
                        continue
                    catches.append({"file": path, "line": n, "category": name, "why": c.get("why"),
                                    "shape": mask(m.group(), c.get("mask") or "digits")})
            if derived:
                digests, key = derived
                for tok in _tokens(line):
                    if _digest(key, tok) in digests and not allowed(cfg, path, line, "derived"):
                        catches.append({"file": path, "line": n, "category": f"derived:{tok.split(':')[0]}",
                                        "why": "matches a value on the principal's natal record",
                                        "shape": tok.split(":")[0] + ":" + mask(tok.split(":", 1)[1], "digits")})
    return catches


def log_catches(catches, mode):
    if not catches:
        return
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    fd = os.open(CATCHES, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        for c in catches:
            fh.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "mode": mode, **c},
                                ensure_ascii=False) + "\n")


def promote(cfg):
    try:
        with open(CATCHES, encoding="utf-8") as fh:
            caught = [json.loads(x) for x in fh if x.strip()]
    except OSError:
        caught = []
    have = {(c["category"], c["shape"]) for c in cfg.get("learned_cases") or []}
    new = [{"category": c["category"], "shape": c["shape"], "first_seen": c["at"][:10]}
           for c in caught if not c["category"].startswith("derived:")
           and (c["category"], c["shape"]) not in have]
    uniq = {(c["category"], c["shape"]): c for c in new}
    cfg.setdefault("learned_cases", []).extend(uniq.values())
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return len(uniq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--refresh-derived", action="store_true")
    ap.add_argument("--promote", action="store_true")
    a = ap.parse_args()
    ci = bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))
    cfg, compiled = load_config()
    if a.refresh_derived:
        out = refresh_derived(cfg)
        print(f"derived digests rebuilt: {len(out['digests'])} ({', '.join(out['kinds'])}) - values not stored")
        return 0
    if a.promote:
        print(f"promoted {promote(cfg)} masked near-miss shape(s) into config learned_cases")
        return 0
    fails, n_learned = self_test(cfg, compiled)
    if fails:
        raise Blocked("self-test failed:\n    " + "\n    ".join(fails))
    derived = None
    if ci:
        layer = "derived layer SKIPPED (CI has no state/ - it runs locally and on the pre-commit hook)"
    else:
        try:
            derived = load_derived(cfg)
            layer = f"derived layer: {len(derived[0])} digest(s) of the principal's values"
        except (OSError, ValueError, KeyError) as exc:
            raise Blocked(f"derived digests unavailable locally ({exc}) - run "
                          "`python3 tools/check_personal_data.py --refresh-derived`")
    files = staged_files() if a.staged else tracked_files()
    catches = scan(cfg, compiled, files, a.staged, derived)
    print(f"personal-data firewall: {len(compiled)} categories, {n_learned} remembered case(s), "
          f"{len(files)} file(s); {layer}")
    if catches:
        log_catches(catches, "staged" if a.staged else ("ci" if ci else "tree"))
        for c in catches:
            print(f"  BLOCKED {c['file']}:{c['line']} [{c['category']}] {c['why']} - shape {c['shape']!r}")
        print(f"check_personal_data: {len(catches)} catch(es) - logged to state/personal_data/catches.jsonl")
        return 1
    print("check_personal_data: ALL OK - no personal data found")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Blocked as exc:
        print(f"check_personal_data: BLOCKED - {exc}")
        sys.exit(2)
    except Exception as exc:      # fail closed: a scanner that errors must not pass
        print(f"check_personal_data: BLOCKED - scanner error {type(exc).__name__}: {exc}")
        sys.exit(2)
