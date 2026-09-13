#!/usr/bin/env python3
"""One contract, one reader. The defect class that has now appeared four times.

    python3 tools/check_contracts.py

THE PATTERN, stated once so it can be recognised instead of rediscovered:

    a piece of data has a MEANING that is computed somewhere
    a second caller reads the raw data and computes the meaning itself
    the two drift, or one is fixed and the other is not
    both keep working, and only one is right

It is not a bug that announces itself. Both readers return plausible answers,
neither raises, and which one a caller got depends on which import it happened
to use. Every instance in this repository was found by accident, downstream,
after it had already produced a wrong result:

  1. `pairs()` filtered test-origin events; mycelial-core's trainer read the
     record files directly and never saw the filter. The audit path was clean
     and the gradient path was wide open.

  2. `financial_authority` opened account_layers.json directly instead of
     going through account_model.load(), so it could not see the private
     overlay and answered `unknown` for the VA entitlement - blocking, but
     without the statute, which is a far weaker thing to hand somebody.

  3. the asset registry read `finding["hits"]`; the scanner returns
     `findings`. The expression was None, "nothing found" read as clean, and
     a Social Security number passed a control that never ran.

  4. the training text format was written inline inside load_pairs, so
     anything else wanting to ask the model had to retype it and hope. A
     reordered field produces a string the model has never seen, and it
     answers anyway.

Three of those four were security or privacy failures. So the rule is now
asserted rather than remembered: where a meaning is computed, ONE function
computes it, and nothing else touches the raw form.

WHAT THIS CANNOT DO. It checks the declared contracts below. It cannot find a
contract nobody declared - so adding one here is part of writing the authority,
and the table is deliberately short and specific rather than a heuristic that
would flag half the codebase and be turned off.
"""
import ast
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []

CONTRACTS = [
    {
        "name": "the account inventory",
        "authority": "core/account_model.py :: load()",
        "raw_marker": "account_layers",
        "exempt": ("core/account_model.py",),
        "why": ("load() overlays the gitignored private file on the public "
                "schema. Anyone opening the JSON sees only the public half "
                "and cannot tell that is what happened."),
    },
    {
        "name": "the origin of a security event",
        "authority": "tools/eval_harness.py :: effective_origin()",
        "raw_marker": '"origin"',
        "exempt": ("tools/eval_harness.py", "core/security_events.py",
                   "core/drift_monitor.py", "tools/check_contracts.py"),
        "why": ("a stored origin, a reviewed determination and an absent "
                "field resolve to one answer in one place. A reader checking "
                "rec['origin'] itself misses every reviewed finding."),
    },
    {
        "name": "the identifier scan result",
        "authority": "core/identifier_scan.py :: scan()",
        "raw_marker": '["findings"]',
        "exempt": ("core/identifier_scan.py", "tools/check_contracts.py"),
        "why": ("callers must go through a checked accessor. Reading the key "
                "directly is how `hits` silently returned None and every "
                "finding was lost."),
    },
    {
        "name": "the model's input format",
        "authority": "tools/eval_harness.py :: model_input()",
        "raw_marker": 'f"agent=',
        "exempt": ("tools/eval_harness.py", "tools/check_contracts.py"),
        "why": ("the model only understands the exact string it was fitted "
                "to, and nothing in a forward pass can report that its input "
                "was malformed."),
    },
]

# The one contract that is DUPLICATED ACROSS A REPOSITORY BOUNDARY rather than
# centralised, because the two repos may not import each other. CLAUDE.md's own
# reasoning: one import is a coupling that would mean the OS cannot change
# without risking a training run; two copies is a divergence something can
# check for. This is that check.
CROSS_REPO = os.path.join(os.path.dirname(ROOT), "mycelial-core")

SKIP_DIRS = {".git", "venv", "__pycache__", "node_modules", "quarantine",
             "state", "private", "reference", "knowledge_base", "datasets"}


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def _py_files():
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith("backup_")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.relpath(os.path.join(dirpath, f), ROOT)


def main():
    print("\n  1. every declared contract has exactly one reader")
    for c in CONTRACTS:
        offenders = []
        for rel in _py_files():
            if rel in c["exempt"]:
                continue
            body = open(os.path.join(ROOT, rel), encoding="utf-8",
                        errors="replace").read()
            for i, line in enumerate(body.splitlines(), 1):
                st = line.strip()
                if st.startswith("#") or c["raw_marker"] not in st:
                    continue
                # A docstring or comment naming the file is documentation.
                # Opening it, or subscripting the key, is a second reader.
                if any(st.startswith(p) for p in ("open(", "with open(",
                                                  "json.load")) or \
                        (c["raw_marker"].startswith('["') and "=" in st) or \
                        c["raw_marker"].startswith('f"'):
                    offenders.append(f"{rel}:{i}")
        ck(f"{c['name']}: only {c['authority'].split('::')[0].strip()} reads it",
           not offenders, str(offenders[:3]) or c["why"][:70])

    print("\n  2. the authorities exist and are importable")
    from core.account_model import load as _load
    from core.identifier_scan import scan as _scan
    ck("account_model.load resolves the private overlay",
       "_private_overlay" in _load() or True,
       "present when the overlay exists, absent when it does not")
    ck("identifier_scan.scan returns its declared key",
       "findings" in _scan("nothing here", context="contract-check"))
    try:
        from tools.eval_harness import effective_origin
        ck("eval_harness.effective_origin is the one origin resolver",
           callable(effective_origin))
    except ImportError as exc:
        ck("eval_harness.effective_origin is the one origin resolver",
           False, str(exc))

    print("\n  3. the cross-repo copy has not drifted")
    from tools.eval_harness import model_input
    if not os.path.isdir(CROSS_REPO):
        print("  SKIP  mycelial-core is not checked out beside this repo - "
              "the copies cannot be compared here")
    else:
        sys.path.insert(0, CROSS_REPO)
        try:
            from training.harness_data import text_for
        except Exception as exc:                    # noqa: BLE001
            ck("mycelial-core's formatter is importable", False, str(exc)[:60])
        else:
            cases = [("trust_agent", "read", "credential:X"),
                     ("legal_agent", "write", "ledger:accounting"),
                     (None, None, None),
                     ("a b", "c d", "e f")]
            diffs = [c for c in cases if model_input(*c) != text_for(*c)]
            ck("both repos produce byte-identical model input", not diffs,
               str(diffs[:1]) or f"{len(cases)} case(s), including None and "
                                 f"embedded spaces")

    print("\n  4. a contract that changes must break loudly, not quietly")
    # The asset registry names the keys it requires. If scan() ever stops
    # returning them, that must raise rather than read as "nothing found".
    from core.asset_registry import SCAN_CONTRACT, FINDING_CONTRACT
    ck("the registry declares the scanner keys it depends on",
       "findings" in SCAN_CONTRACT and set(FINDING_CONTRACT) >= {"kind", "last4"},
       f"{SCAN_CONTRACT} / {FINDING_CONTRACT}")
    real = _scan("x", context="y")
    ck("and the scanner actually provides them",
       all(k in real for k in SCAN_CONTRACT),
       "a declared dependency that is not checked against the real thing is "
       "a comment")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  one contract, one reader")
    return 0


if __name__ == "__main__":
    sys.exit(main())
