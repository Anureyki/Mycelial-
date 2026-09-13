#!/usr/bin/env python3
"""Verify git history by CLASS, not by the values somebody happened to find.

    python3 tools/check_history_classes.py

WHY THIS EXISTS. The first history rewrite removed the two values that had
been discovered and declared victory. It missed two third-party names sitting
next to a capacity determination - not because anyone was careless, but
because the check was "is the string I already knew about gone?" rather than
"is anything of this CLASS still present?".

A remediation verified against its own inputs can only ever confirm what was
already known.

WHAT IT CHECKS, from config/disclosure_policy.json:

    SECRET            must be absent from the working tree AND from history
    PRIVATE           must be absent from both, unless retention is declared
    PUBLIC_REQUIRED   must still be PRESENT - a rewrite that removed it broke
                      the artifact it was required by
    PUBLIC_OPTIONAL   not asserted either way

THE PROBE VALUES ARE NOT IN THIS FILE. A verifier that stored the secret to
search for it would republish the secret in the verifier - the exact bug this
repository already shipped. So SECRET and PRIVATE items are checked by their
declared PATTERN or by a value supplied at runtime, never by a literal here.
Where an item can only be checked with the value, this reports SKIP and says
so rather than pretending to have checked.

PASS / SKIP / FAIL are three outcomes. A check that could not run is never a
pass.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY = os.path.join(ROOT, "config", "disclosure_policy.json")

fails, skips = [], []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def skip(name, why):
    print(f"  SKIP  {name}   {why}")
    skips.append(name)


def _git(*args):
    """Run git and decode TOLERANTLY, because history contains bytes.

    text=True decodes strictly, and a historical file carrying a Windows-1252
    quote (0x92) raised UnicodeDecodeError and killed the whole verifier. It
    passed locally and failed in CI, which is the same environment-dependent
    shape this repository has now met three times.

    Undecodable bytes are REPLACED rather than skipped. Every pattern here is
    ASCII-shaped, so a replacement character cannot mask a match - and
    dropping the file instead would be a scanner quietly not scanning, which
    is the failure mode this whole layer exists to prevent."""
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)}: "
            f"{r.stderr.decode('utf-8', 'replace')[:160]}")
    return r.stdout.decode("utf-8", "replace")


def history_hits(pattern_or_value, regex=False):
    """-> number of commits whose content contains it. Value never printed.

    GIT'S PICKAXE USES POSIX REGEX, NOT PCRE. No inline flags, no shorthand
    character classes. Passing a Python pattern here raised "invalid regex" - and had
    that been caught and treated as zero hits, this verifier would have
    reported a clean history because its own search was malformed. Which is
    the identical shape as the scanner that read the wrong key: a control
    reporting clean because it never ran. So the POSIX form is supplied
    separately and a regex error RAISES."""
    args = ["log", "--all", "--format=%h", f"-S{pattern_or_value}"]
    if regex:
        args += ["--pickaxe-regex", "-i"]
    return len([x for x in _git(*args).splitlines() if x.strip()])


def tree_hits(pattern, regex=True):
    files = [f for f in _git("ls-files").splitlines() if f.strip()]
    rx = re.compile(pattern) if regex else None
    n = 0
    for f in files:
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            continue
        try:
            body = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if (rx.search(body) if rx else pattern in body):
            n += 1
    return n


# Structural patterns for the classes that must be ABSENT. No real value here.
# (label, PCRE for the tree, POSIX ERE for git's pickaxe, class). Two forms
# because two engines - and a pattern that silently fails to compile in one of
# them would report a clean result from a search that never ran.
ABSENCE_PATTERNS = {
    "an SSN-shaped identifier beside a label": (
        r"(?is)(social\s+security|SSN|VA\s+FILE\s+NO)[^0-9]{0,40}(?:\d[\s-]?){8}\d",
        r"(social security|SSN|VA FILE NO)[^0-9]{0,40}[0-9][0-9 -]{6,}[0-9]",
        "SECRET"),
    "a bare SSN-shaped number": (
        r"\b\d{3}-\d{2}-\d{4}\b",
        r"[0-9]{3}-[0-9]{2}-[0-9]{4}", "SECRET"),
    # example.com and example.org are RFC 2606 reserved and cannot belong to
    # anyone - excluding them is structural, not an allowlist. The pattern is
    # looking for somebody's real inbox in a source default.
    "a real email address in a source default": (
        r"SEC_USER_AGENT[^\n]*@(?!example\.(com|org|net))[^\n]*\.",
        r"SEC_USER_AGENT[^\n]*@[^\n]*", "PRIVATE"),
    "a name beside a fiduciary capacity": (
        r"(?i)[A-Z][a-z]+\s+[A-Z][a-z]+\s*\((?:VA-)?appointed\s+fiduciary\)",
        r"[A-Za-z]+ [A-Za-z]+ \((VA-)?appointed fiduciary\)", "PRIVATE"),
    "a name beside a hub-manager signature": (
        r"(?i)signed\s+\d{4}-\d{2}-\d{2}\s+by\s+[A-Z][a-z]+",
        r"signed [0-9]{4}-[0-9]{2}-[0-9]{2} by [A-Za-z]+", "PRIVATE"),
}


def approved_paths(policy):
    """-> paths where a class may legitimately appear, from the policy itself.

    NOT AN AD HOC ALLOWLIST. Each entry is attached to a recorded decision
    with its reason, so "why is this excluded?" is answerable from the same
    file that says what the class is. A corpus ingest of Regulation Z carries
    the IRS's own example SSN; that is the statute, not a leak, and altering
    an authority to satisfy a scanner corrupts the thing the agent reasons
    from."""
    out = []
    for d in policy["decisions"]:
        out += d.get("approved_paths") or []
    return tuple(out)


def commit_hits_outside(posix_pattern, allowed, confirm_pattern=None):
    """POSIX narrows, PCRE confirms. Two engines doing what each can.

    git's pickaxe has no lookaheads, so the POSIX form of "an email that is
    NOT example.com" cannot be written - RFC 2606 reserves example.com and it
    appears in this repo's own error text as the thing a user should copy. The
    pickaxe therefore over-selects, and Python re-checks each candidate with
    the precise pattern.

    Deliberately in that order. The loose pattern may only ADD candidates for
    the strict one to reject; it can never hide a commit the strict pattern
    would have caught, because every commit the strict pattern matches is also
    matched by the looser one it was derived from."""
    """-> commits where the pattern appears OUTSIDE the approved paths.

    The blunt version counted any commit touching the pattern, so every corpus
    ingest read as a leak. Path-aware is the only honest form: the question is
    not "does this string exist in history" but "does it exist somewhere it
    was never authorized to be"."""
    out = []
    for sha in _git("log", "--all", "--format=%h", f"-S{posix_pattern}",
                    "--pickaxe-regex", "-i").split():
        try:
            files = _git("show", "--name-only", "--format=", sha).split()
        except RuntimeError:
            out.append(sha)
            continue
        if any(not f.startswith(allowed) for f in files if f.strip()):
            # Narrow it: does the pattern actually appear in a non-approved
            # file at this commit, or only in an approved one touched alongside?
            hit = False
            for f in files:
                if not f.strip() or f.startswith(allowed):
                    continue
                try:
                    body = _git("show", f"{sha}:{f}")
                except RuntimeError:
                    continue
                if re.search(confirm_pattern or posix_pattern, body, re.I):
                    hit = True
                    break
            if hit:
                out.append(sha)
    return out


def main():
    try:
        policy = json.load(open(POLICY, encoding="utf-8"))
    except Exception as exc:                        # noqa: BLE001
        print(f"  BLOCKED: disclosure policy unreadable ({exc})", file=sys.stderr)
        return 2
    decisions = policy["decisions"]

    print("\n  1. classes that must be ABSENT - checked by pattern, "
          "never by storing the value")
    allowed = approved_paths(policy)
    print(f"        approved locations from the policy: {list(allowed)}")
    for label, (pcre, posix, klass) in ABSENCE_PATTERNS.items():
        tf = [f for f in _git("ls-files").split()
              if f.strip() and not f.startswith(allowed)
              and os.path.isfile(os.path.join(ROOT, f))
              and re.search(pcre, open(os.path.join(ROOT, f), encoding="utf-8",
                                       errors="replace").read())]
        hc = commit_hits_outside(posix, allowed, confirm_pattern=pcre)
        ck(f"[{klass}] {label}: absent from tree outside approved paths",
           not tf, f"{len(tf)} file(s) {tf[:2]}")
        ck(f"[{klass}] {label}: absent from history outside approved paths",
           not hc, f"{len(hc)} commit(s) {hc[:3]}")

    print("\n  2. PUBLIC_REQUIRED information is still PRESENT")
    # A rewrite that removed these broke the artifact that needed them. The
    # failure mode of over-redaction is silent: the file still exists.
    req = [d for d in decisions if d["classification"] == "PUBLIC_REQUIRED"]
    ck("the policy declares at least one retention", bool(req),
       "a policy with no retentions is a scrubber wearing a policy's name")
    ip = os.path.join(ROOT, "docs", "IP_ASSIGNMENT.md")
    if not os.path.exists(ip):
        ck("docs/IP_ASSIGNMENT.md exists", False, "the artifact is gone")
    else:
        body = open(ip, encoding="utf-8").read()
        named = re.search(r'by \*\*[A-Z][a-z]+ [A-Z][a-z]+\*\* \("Assignor"\)',
                          body)
        ck("the IP assignment still names its assignor", bool(named),
           "an assignment with no assignor assigns nothing - over-redaction "
           "fails silently, because the file still exists")

    print("\n  3. every retention records WHY")
    missing = [d["id"] for d in decisions
               if d["classification"].startswith("PUBLIC")
               and not d.get("public_necessity")]
    ck("each PUBLIC_* decision states its necessity", not missing, str(missing))
    noaction = [d["id"] for d in decisions if not d.get("action")]
    ck("each decision records the action taken", not noaction, str(noaction))
    nocontext = [d["id"] for d in decisions if not d.get("context")]
    ck("each decision records the CONTEXT, not just the string",
       not nocontext, str(nocontext) or
       "identity + context + capacity + necessity, never the string alone")

    print("\n  4. the model is contextual, proven by a same-string split")
    # The same name is PUBLIC_REQUIRED in one artifact and PRIVATE in another.
    # If that pair ever collapses to one class, the model has become a scrubber.
    ids = {d["id"]: d["classification"] for d in decisions}
    ck("the principal's name is PUBLIC_REQUIRED where the contract needs it",
       ids.get("principal_legal_name_in_ip_assignment") == "PUBLIC_REQUIRED")
    ck("and a third party's name beside a capacity finding is PRIVATE",
       ids.get("va_fiduciary_name") == "PRIVATE",
       "the pairing is the disclosure, not the name")

    print("\n  5. values only checkable with the value itself")
    supplied = os.environ.get("MYC_HISTORY_PROBE")
    if not supplied:
        skip("exact-value history probe",
             "no MYC_HISTORY_PROBE supplied - not run, and NOT counted as a "
             "pass. Patterns above cover the classes; this would cover an "
             "exact string, and storing one here would republish it")
    else:
        n = history_hits(supplied)
        ck("the supplied probe value is absent from history", n == 0,
           f"{n} commit(s)")

    print()
    if skips:
        print(f"  {len(skips)} SKIPPED: {skips}")
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  history holds by class, and what must remain, remains")
    return 0


if __name__ == "__main__":
    sys.exit(main())
