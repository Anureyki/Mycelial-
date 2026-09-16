#!/usr/bin/env python3
"""Refuse to let sensitive material become a commit. Runs BEFORE it exists.

    python3 tools/check_no_secrets.py            # the working tree
    python3 tools/check_no_secrets.py --staged   # what is about to be committed

WHY BEFORE AND NOT AFTER. A secret caught by CI is a secret that has already
been pushed - and once it is public, deleting it is not undoing it. This
repository learned that with a real Social Security number: redacting the file
left it in fourteen commits, and clearing it needed a history rewrite and a
force-push. The only cheap moment is the one before `git commit` returns.

So this is wired as a pre-commit hook. CI runs it too, as a backstop for a
commit made with --no-verify or on another machine, but the hook is the point.

IT DETECTS BY PATTERN, NEVER BY REMEMBERING THE VALUE. Nothing here contains
the principal's identifiers. A scanner that hardcodes the secret it looks for
has republished the secret in the scanner - which is EXACTLY the bug this repo
already shipped, where the module written to detect an SSN carried his own in
a comment as the example. Patterns only. Forever.

FAIL CLOSED. Any failure of this check - an exception, an unreadable file, a
scanner contract that has changed - BLOCKS the commit. A secret scanner that
errors and lets the commit through is worse than none, because it is trusted.
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# This began as a pure pattern scanner that imported nothing. It now imports
# core.identifier_scan for the canonical Luhn check, so it declares itself a
# test the same as every other gate - the rule in check_eval.py asserts the
# DECLARATION rather than the current call graph, precisely because a file
# that grows an import will not come with a reminder.
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
sys.path.insert(0, ROOT)

# Paths whose CONTENT is personal by definition. Staging one at all is the
# finding - there is no need to look inside, and looking inside a file that
# should never be committed is how its contents end up in a log.
NEVER_COMMIT_PATHS = (
    "private/", "datasets/", "knowledge_base/", "state/", "weights/",
    "sensor_data/",
)
# Exact filenames, not prefixes: ".env" as a prefix flags ".env.example",
# which is a committed TEMPLATE holding variable names and no values - and
# teaching people to ignore this scanner's output is the fastest way to make
# it useless.
NEVER_COMMIT_FILES = (".env",)

# Structural patterns. No real value appears here.
PATTERNS = {
    "ssn_or_file_number": (
        re.compile(r"(?is)(social\s+security|SSN|VA\s+FILE\s+NO|FILE\s+NUMBER|"
                   r"CLAIM\s+NUMBER)[^0-9]{0,40}((?:\d[\s-]?){8}\d)"),
        "a Social Security or VA file number"),
    "bare_ssn": (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
                 "something shaped like an SSN"),
    "dod_id": (re.compile(r"(?is)(EDIPI|DoD\s*ID)[^0-9]{0,20}\d{10}"),
               "a DoD ID / EDIPI"),
    # Validated with Luhn below, not just matched. The bare length pattern
    # flagged a LIST OF PORT NUMBERS in start_all.sh and several passages of
    # statutory text - and a scanner that cries wolf on a port list is a
    # scanner people learn to run with their eyes shut. A real card number
    # satisfies Luhn; four-digit groups that happen to sit next to each other
    # do not.
    # STANDALONE, then Luhn. Two lookarounds do most of the work:
    #   (?<![\d.])  rejects a run inside a float - 18.400000000000006 was
    #               flagged as a card number because its mantissa is long
    #   (?![\s\d-]*\d)  rejects a group that continues into more numbers,
    #               which is how a list of health-check ports matched a
    #               4-4-4-4 card shape
    # A scanner that fires on a float and a port list is one people learn to
    # ignore, and an ignored scanner is worse than an absent one because it is
    # counted as protection.
    # AND IT MUST START LIKE A CARD. Every issuer number begins 3, 4, 5 or 6
    # (Amex/Diners, Visa, Mastercard, Discover). That last discriminator is
    # what separates a card from the tail of a health-check port list -
    # 9010 9011 9012 9013 is Luhn-valid by coincidence - and from an Internet
    # Archive scan identifier in a 1886 treatise. Three independent conditions
    # now have to coincide: card-shaped, issuer-prefixed, Luhn-valid.
    "card_number": (re.compile(
        r"(?<![\d.])(?:[3-6]\d{12,18}|[3-6]\d{3}[ -]\d{4}[ -]\d{4}[ -]\d{4})"
        r"(?![\d.])(?![\s-]*\d)"),
        "a payment card number (issuer-prefixed, standalone, Luhn-valid)"),
    "routing": (re.compile(r"(?is)(routing|ABA)[^0-9]{0,20}\d{9}\b"),
                "a bank routing number"),
    "private_key": (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
                    "a private key"),
    "aws_key": (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    "github_token": (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
                     "a GitHub token"),
    "openai_key": (re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"), "an API key"),
    "assigned_secret": (
        re.compile(r"(?i)\b(password|passwd|secret|api_?key|token|"
                   r"mfa_secret|totp)\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
        "a credential assigned in source"),
}

# Files allowed to contain a pattern, with the reason. Narrow on purpose: an
# allowlist that grows without reasons is a disabled scanner.
ALLOWED = {
    "tools/check_no_secrets.py": "the scanner's own patterns",
    "tools/check_asset_registry.py": "documented synthetic test values",
    "core/identifier_scan.py": "the detector's own patterns",
    "core/asset_registry.py": "field-name refusal list",
    "tools/check_retrieval.py": "a zero-filled citation placeholder",
    "reference/accounting_agent/internal_revenue_manual_part_5.json":
        "the IRS's own published example SSN inside the Internal Revenue Manual",
    "tools/check_ingest.py":
        "synthetic fixtures proving the write guard reaches the ingestion "
        "path - a documented example SSN and a documented test card in an "
        "extracted value and a quoted original text, never real values",
    "tools/check_outcome_loop.py":
        "one synthetic fixture (the SSA's documented example number) proving "
        "a prediction carrying an identifier is refused before it is written",
    "tools/check_complaint_lane.py":
        "one synthetic fixture (the SSA's documented example number) proving "
        "a complaint carrying an identifier is refused before it is written",
    "tools/check_tcpa_lane.py":
        "one synthetic fixture (the SSA's documented example number) proving "
        "a call note carrying an identifier is refused before it is written",
    "tools/check_fcra_furnisher.py":
        "one synthetic fixture (the SSA's documented example number) proving "
        "evidence carrying an identifier is refused before it is written",
    "tools/check_dispute_letters.py":
        "synthetic fixtures (the SSA's documented example number and a "
        "documented test card) proving a letter refuses an identifier",
    "tools/check_custody.py":
        "one synthetic fixture (the SSA's documented example number) proving "
        "a custody event carrying an identifier is refused before it is written",
    "tools/check_contract_engine.py":
        "one synthetic fixture (the SSA's documented example number) proving "
        "a training pair carrying an identifier is refused at the door",
    "tools/check_ontology.py":
        "synthetic fixtures proving the shared write guard reaches the "
        "counterparty and contract registries - a documented example SSN and "
        "a documented test card, never real values",
    "tools/check_staging_boundary.py":
        "the synthetic fixture this gate stages on purpose to prove that a "
        "forced add is still refused. It is the documented example SSN and "
        "never a real value - testing a secret scanner must not require the "
        "secret",
    "config/disclosure_policy.json":
        "the disclosure policy names the synthetic example it approves. A "
        "policy that cannot say WHICH value it approved is not auditable",
    "reference/_shared/pomeroy_equity_jurisprudence_vol_1_1886.json":
        "an Internet Archive scan identifier in the provenance line of an 1886 "
        "public-domain treatise. It is card-shaped, issuer-prefixed AND "
        "Luhn-valid by coincidence - all three conditions at once, which is "
        "roughly what you would expect to happen once across a corpus this "
        "size, and is why this list exists with reasons rather than as a "
        "silent exclusion",
}

SKIP_DIRS = {".git", "venv", "__pycache__", "node_modules", "quarantine"}


# ONE LUHN, IMPORTED. This had its own copy while core/asset_registry.py had
# none, and the gap between the two let a card number through a registry write
# in a prose field. The canonical detector now lives in core/identifier_scan.py
# - the module whose job is identifiers - and both callers use it.
sys.path.insert(0, ROOT)
from core.identifier_scan import luhn as _luhn          # noqa: E402


def _staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only",
                          "--diff-filter=ACM"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git diff --cached failed: {out.stderr[:200]}")
    return [f for f in out.stdout.splitlines() if f.strip()]


def _tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {out.stderr[:200]}")
    return [f for f in out.stdout.splitlines() if f.strip()]


def _staged_blob(rel):
    """-> the content GIT WILL COMMIT for this path, from the index.

    NOT THE FILE ON DISK. This is the bypass the boundary test found: the
    scanner listed staged filenames and then opened them from the working
    tree. Stage a secret, clean the file, commit - the index still carries the
    secret, the scan reads the clean disk copy, and the commit lands.

    The index is what becomes the commit. The working tree is a different
    thing that usually happens to match, and "usually happens to match" is not
    a security property.

    A blob that cannot be read RAISES, so it is refused rather than skipped.
    """
    r = subprocess.run(["git", "show", f":{rel}"], cwd=ROOT,
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"cannot read the staged blob for {rel}: "
            f"{r.stderr.decode('utf-8', 'replace')[:160]}")
    return r.stdout.decode("utf-8", "replace")


def scan_paths(paths, from_index=False):
    """-> [findings]. A finding NEVER carries the value it matched."""
    findings = []
    for rel in paths:
        for bad in NEVER_COMMIT_PATHS + NEVER_COMMIT_FILES:
            if rel.startswith(bad) if bad.endswith("/") else rel == bad or \
                    os.path.basename(rel) == bad:
                findings.append({
                    "file": rel, "what": f"a path under {bad}",
                    "why": ("This location holds personal or runtime data and "
                            "is gitignored. Staging it at all is the finding; "
                            "its contents are not inspected, because reading a "
                            "file that must never be committed is how it ends "
                            "up in a log."),
                })
                break
        else:
            full = os.path.join(ROOT, rel)
            if any(p in full.split(os.sep) for p in SKIP_DIRS):
                continue
            if from_index:
                body = _staged_blob(rel)
            else:
                if not os.path.isfile(full):
                    continue
                try:
                    body = open(full, encoding="utf-8", errors="replace").read()
                except OSError as exc:
                    raise RuntimeError(f"{rel} unreadable: {exc}") from exc
            for name, (rx, human) in PATTERNS.items():
                m = None
                for cand in rx.finditer(body):
                    if name == "card_number" and not _luhn(cand.group(0)):
                        continue
                    m = cand
                    break
                if not m:
                    continue
                if rel in ALLOWED:
                    continue
                line = body[:m.start()].count("\n") + 1
                findings.append({
                    "file": f"{rel}:{line}", "what": human,
                    # The matched text is NOT reported. A scanner that prints
                    # what it caught has moved the secret into the terminal,
                    # the CI log and the scrollback.
                    "why": f"matched the {name} pattern; value withheld",
                })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true",
                    help="check what is about to be committed")
    a = ap.parse_args()
    try:
        paths = _staged_files() if a.staged else _tracked_files()
        # --staged reads the INDEX. Anything else reads the working tree.
        findings = scan_paths(paths, from_index=a.staged)
    except Exception as exc:                        # noqa: BLE001
        # FAIL CLOSED. A scanner that errors and lets the commit through is
        # worse than no scanner, because it is trusted.
        print(f"  BLOCKED: the secret scan could not complete "
              f"({type(exc).__name__}: {exc}). Nothing is assumed clean.",
              file=sys.stderr)
        return 2
    what = "staged for commit" if a.staged else "tracked in the repository"
    if not findings:
        print(f"  no sensitive patterns in {len(paths)} file(s) {what}")
        return 0
    print(f"\n  BLOCKED - {len(findings)} finding(s) in what is {what}:\n",
          file=sys.stderr)
    for f in findings:
        print(f"    {f['file']}\n      {f['what']} - {f['why']}",
              file=sys.stderr)
    print("\n  This repository is PUBLIC. Move the value into private/ or an "
          "environment variable, or record a last-four and a document "
          "reference instead of the identifier.\n"
          "  If this is a genuine false positive, add the path to ALLOWED in "
          "tools/check_no_secrets.py WITH THE REASON.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
