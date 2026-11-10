#!/usr/bin/env python3
"""Build gate: what Legal tells a model lives in agents/legal_agent/legal.md, and the
file and the code agree (principal, 2026-11-10, option A increment 2).

Proves: every section the code asks for exists, and every section in the file is used;
each call fills exactly the template's placeholders; the preamble - Legal's statute and
CFR rules as told to the model - rides on every model call; no prompt text is left
inline; an edit to the file takes effect without a restart; and a duplicate section,
a missing section or an unfilled placeholder is refused rather than sent half-built.
"""
import ast
import os
import re
import string
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
SRC = os.path.join(ROOT, "agents", "legal_agent", "legal_agent.py")
MD = os.path.join(ROOT, "agents", "legal_agent", "legal.md")
fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


from agents.legal_agent.legal_agent import LegalAgent  # noqa: E402
secs = LegalAgent._behavior_sections()
src = open(SRC, encoding="utf-8").read()
tree = ast.parse(src)
calls = {}
for n in ast.walk(tree):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("_behavior", "_behavior_lines") \
            and n.args and isinstance(n.args[0], ast.Constant):
        calls.setdefault(n.args[0].value, []).append({k.arg for k in n.keywords})
check("every section the code asks for is in legal.md", set(calls) <= set(secs))
check("every section in legal.md is used by the code" + (f" (unused: {sorted(set(secs) - set(calls))})" if set(secs) - set(calls) else ""),
      set(secs) <= set(calls))


def placeholders(t):
    return {m.group("named") or m.group("braced") for m in string.Template.pattern.finditer(t)
            if m.group("named") or m.group("braced")}


bad = [(name, sorted(kw), sorted(placeholders(secs[name]))) for name, kws in calls.items() if name in secs
       for kw in kws if kw != placeholders(secs[name])]
check("each call fills exactly its template's placeholders" + (f" {bad}" if bad else ""), not bad)
check("the preamble rides on every model call (attached in _call_inference)",
      'prompt = self._behavior("preamble") + "\\n\\n" + str(prompt)' in src)
pre = secs.get("preamble", "")
check("the preamble states the statute and CFR rules the code enforces",
      "authority only when its text is in front of you" in pre and "report it exactly as written" in pre
      and "An empty field is a known gap" in pre)
check("no prompt text is left inline in legal_agent.py",
      not any(m in src for m in ("You are a private-ordering", "You are a legal case analyst", "Format this docket entry",
                                 "Reflect on this completed", "Briefly define or explain", "Review this filing draft")))
a = LegalAgent.__new__(LegalAgent)
try:
    a._behavior("define_term")
    check("an unfilled placeholder is refused", False)
except KeyError:
    check("an unfilled placeholder is refused", True)
try:
    a._behavior("nope")
    check("a missing section is refused", False)
except KeyError:
    check("a missing section is refused", True)
tmp = os.path.join(tempfile.mkdtemp(), "legal.md")
orig = LegalAgent.LEGAL_MD
try:
    LegalAgent.LEGAL_MD = tmp
    open(tmp, "w").write("## define_term\n\n```text\nOne: $term\n```\n")
    first = a._behavior("define_term", term="x")
    os.utime(tmp, (1, 1))
    open(tmp, "w").write("## define_term\n\n```text\nTwo: $term\n```\n")
    check("an edit to the file is read on the next call, no restart", first == "One: x" and a._behavior("define_term", term="x") == "Two: x")
    open(tmp, "w").write("## a\n\n```text\nx\n```\n## a\n\n```text\ny\n```\n")
    os.utime(tmp, (2, 2))
    try:
        LegalAgent._behavior_sections()
        check("a section defined twice is refused", False)
    except ValueError:
        check("a section defined twice is refused", True)
finally:
    LegalAgent.LEGAL_MD = orig
    LegalAgent._behavior_cache.update(mtime=None, sections={})
print("check_legal_behavior: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
