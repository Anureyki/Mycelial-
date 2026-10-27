#!/usr/bin/env python3
"""What each declared verb DOES to the world, read from its code.

    from core.effects import detect, CLASSES

An agent card listed 117 verbs for Grow with nothing to say which of them only
reason and which change something - a reading recorded, a message sent, a
device moved. The card is how Anansi finds who can answer, so every verb stays
declared; the effect class is a second field beside it (`effects` on the card),
and this module is how that field is checked against the code rather than
believed. A class set by hand is a claim; the code is the evidence - the same
rule as authority_class: set from the content, never from the name.

Classes, lowest to highest - a verb takes the highest anywhere on its path:

    computes   reasoning only; nothing is written
    records    writes the agent's own store (memory, a ledger, a log file)
    notifies   reaches the principal (Anansi's notify)
    actuates   moves a device - through the HAL, behind Boss's authorization
    external   reaches a third party - refused without the principal's sign-off

Static analysis, deliberately conservative: an a2a call whose task this file
cannot prove is a read counts as `records`, so a verb is never under-classed
by a guess. Over-classing is the safe error; the reviewed card can say why.
"""
import ast
import os

CLASSES = ("computes", "records", "notifies", "actuates", "external")
RANK = {c: i for i, c in enumerate(CLASSES)}

# a2a tasks known to only read - anything else sent to a peer counts as a write
READ_TASKS = {"lookup", "retrieve_memory", "retrieve_many", "knowledge_search", "search_docs",
              "get_audit_digests", "get_progress_summary", "search", "query", "search_memory",
              "check_budget_constraint", "authorize_route", "check_guard",
              "routing_terms", "roles", "describe", "answer", "list_lessons",
              "check_permission", "get_status", "health", "pulse", "roster",
              "reasoning", "reason", "generate", "infer", "inference", "complete",
              "chat", "embed", "classify", "vision", "detect", "caption"}
# Operational logging is execution history, kept apart from domain records by
# design (CLAUDE.md, Ownership model) - a log line is not a change to the grow.
NOT_AN_EFFECT = {"log", "_log", "log_event", "audit"}
WRITE_SELF = {"store_own_memory", "amend_grow_system", "record_provenance_event",
              "pin_memory", "delete_own_memory"}
EXTERNAL_MODULES = {"smtplib"}
PROJECT = ("core.", "tools.")
ACTUATE_HINTS = {"set_state", "set_device_state", "actuate", "publish_command"}


def _max(a, b):
    return a if RANK[a] >= RANK[b] else b


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class _Direct(ast.NodeVisitor):
    """Direct effects and self-calls inside one function body."""

    def __init__(self, aliases=None, fromnames=None, module=None, local_defs=()):
        self.effect, self.calls, self.why, self.tasks = "computes", set(), [], set()
        # PROJECT MODULES ARE PART OF THE PATH (2026-10-27): Legal's ssa_demand_*
        # verbs write their ledger from core/ssa_form_demand.py; stopping at the
        # agent's own methods would have labelled them compute-only.
        self.mods = set()
        self.alias = dict(aliases or {})          # name -> "core.x"
        self.fromnames = dict(fromnames or {})    # name -> ("core.x", "f")
        self.module, self.local_defs = module, set(local_defs)

    def _hit(self, cls, why):
        if RANK[cls] > RANK[self.effect]:
            self.effect = cls
        self.why.append(why)

    def visit_Call(self, n):
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in self.alias:
            self.mods.add((self.alias[f.value.id], f.attr))
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)
                and isinstance(f.value.value, ast.Name) and f.value.value.id == "core"):
            self.mods.add((f"core.{f.value.attr}", f.attr))
        if isinstance(f, ast.Name) and f.id in self.fromnames:
            self.mods.add(self.fromnames[f.id])
        if isinstance(f, ast.Name) and self.module and f.id in self.local_defs:
            self.mods.add((self.module, f.id))
        on_self = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self"
        if on_self and name in NOT_AN_EFFECT:
            return
        if on_self and name:
            self.calls.add(name)
            if name in WRITE_SELF:
                self._hit("records", name)
            if name == "handle_task" and n.args and _const(n.args[0]):
                self.tasks.add(_const(n.args[0]))
            if name == "send_a2a" and len(n.args) >= 2:
                peer, task = _const(n.args[0]), _const(n.args[1])
                if peer == "anansi" and task == "notify":
                    self._hit("notifies", "send_a2a(anansi, notify)")
                elif task and task in READ_TASKS:
                    pass
                elif task and task.startswith(("get_", "list_", "lookup", "check_", "assess")):
                    pass
                else:
                    self._hit("records", f"send_a2a({peer}, {task})")
        if name in ("open",) and not on_self:
            mode = _const(n.args[1]) if len(n.args) > 1 else None
            for kw in n.keywords:
                if kw.arg == "mode":
                    mode = _const(kw.value)
            if mode and any(c in mode for c in "wax+"):
                self._hit("records", f"open(..., {mode!r})")
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "os":
            if f.attr in ("replace", "rename", "remove", "unlink"):
                self._hit("records", f"os.{f.attr}")
            if f.attr == "open":
                self._hit("records", "os.open")
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "shutil":
            self._hit("records", f"shutil.{f.attr}")
        if name in ACTUATE_HINTS:
            self._hit("actuates", name)
        if isinstance(f, ast.Attribute) and f.attr == "sendmail":
            self._hit("external", "sendmail")
        self.generic_visit(n)

    def visit_Import(self, n):
        for a in n.names:
            if a.name in EXTERNAL_MODULES:
                self._hit("external", f"import {a.name}")
            if a.name.startswith(PROJECT) and a.asname:
                self.alias[a.asname] = a.name

    def visit_ImportFrom(self, n):
        mod = n.module or ""
        for a in n.names:
            if mod in ("core", "tools"):
                self.alias[a.asname or a.name] = f"{mod}.{a.name}"
            elif mod.startswith(PROJECT):
                self.fromnames[a.asname or a.name] = (mod, a.name)

    def visit_FunctionDef(self, n):
        # nested functions run as part of the body that defines them
        self.generic_visit(n)


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the project's own modules; tools/ is in it because Legal's acquire_opinion
# shelves an opinion through tools/ingest_law.shelve (2026-10-27)
_MOD_CACHE = {}


def _module(modname):
    """-> (top-level defs, aliases, fromnames) for a project core module."""
    if modname in _MOD_CACHE:
        return _MOD_CACHE[modname]
    path = os.path.join(_ROOT, *modname.split(".")) + ".py"
    if not modname.startswith(PROJECT) or not os.path.exists(path):
        _MOD_CACHE[modname] = ({}, {}, {})
        return _MOD_CACHE[modname]
    tree = ast.parse(open(path, encoding="utf-8").read())
    v = _Direct()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            v.visit(node)
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs.setdefault(f"{node.name}.{m.name}", m)
                    defs.setdefault(m.name, m)
    _MOD_CACHE[modname] = (defs, v.alias, v.fromnames)
    return _MOD_CACHE[modname]


def _module_imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    v = _Direct()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            v.visit(node)
    return v.alias, v.fromnames


def _methods(path, class_name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {m.name: m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return {}


def _branches(fn):
    """task -> list of statements, from `if task == "x"` / `task in (...)` /
    `task.startswith("x")` tests anywhere in a dispatcher."""
    out = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        names = []
        if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "task":
            for op, c in zip(t.ops, t.comparators):
                if isinstance(op, ast.Eq) and _const(c):
                    names.append(_const(c))
                if isinstance(op, ast.In) and isinstance(c, (ast.Tuple, ast.List, ast.Set)):
                    names += [_const(e) for e in c.elts if _const(e)]
        if (isinstance(t, ast.Call) and isinstance(t.func, ast.Attribute) and t.func.attr == "startswith"
                and isinstance(t.func.value, ast.Name) and t.func.value.id == "task" and t.args):
            pref = _const(t.args[0])
            if pref:
                names.append(pref + "*")
        for nm in names:
            out.setdefault(nm, []).extend(node.body)
    return out


def detect(agent_path, class_name, base_path=None, dispatchers=("handle_task",)):
    """-> {verb: {"effect": class, "why": [...]}} for every verb the dispatchers
    branch on, with effects followed through self-calls (inherited from the base
    class where the agent does not override) and through handle_task re-entry."""
    base_path = base_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "base_agent.py")
    methods = dict(_methods(base_path, "AgentBase"))
    methods.update(_methods(agent_path, class_name))
    a_alias, a_from = _module_imports(agent_path)
    direct = {}
    for name, fn in methods.items():
        v = _Direct(a_alias, a_from)
        for stmt in fn.body:
            v.visit(stmt)
        direct[name] = v

    mod_memo = {}

    def of_mod(modname, fname, seen):
        key = (modname, fname)
        if key in mod_memo:
            return mod_memo[key]
        if key in seen:
            return "computes", []
        defs, al, fr = _module(modname)
        fn = defs.get(fname)
        if fn is None:
            mod_memo[key] = ("computes", [])
            return mod_memo[key]
        v = _Direct(al, fr, module=modname, local_defs=defs.keys())
        for stmt in fn.body:
            v.visit(stmt)
        eff, why = v.effect, [f"{modname}.{fname}: {w}" for w in v.why]
        for m2 in v.mods:
            e2, w2 = of_mod(m2[0], m2[1], seen | {key})
            if RANK[e2] > RANK[eff]:
                eff = e2
            if RANK[e2] > 0:
                why += w2[:1]
        mod_memo[key] = (eff, why)
        return eff, why

    branches, bctx = {}, {}
    for d in dispatchers:
        for src in (_methods(base_path, "AgentBase").get(d), _methods(agent_path, class_name).get(d)):
            if src is not None:
                # a branch sees the imports its dispatcher made above it
                imp = _Direct(a_alias, a_from)
                for node in ast.walk(src):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        imp.visit(node)
                for k, body in _branches(src).items():
                    branches.setdefault(k, []).extend(body)
                    al, fr = bctx.get(k, ({}, {}))
                    bctx[k] = ({**al, **imp.alias}, {**fr, **imp.fromnames})

    memo = {}

    def of_method(name, seen):
        if name in memo:
            return memo[name]
        if name in seen or name not in direct:
            return "computes", []
        seen = seen | {name}
        d = direct[name]
        eff, why = d.effect, [f"{name}: {w}" for w in d.why]
        for c in d.calls:
            if c == "handle_task":
                continue
            e2, w2 = of_method(c, seen)
            if RANK[e2] > RANK["computes"]:
                eff = _max(eff, e2)
                why += w2[:2]
        for t in d.tasks:
            e2, w2 = of_verb(t, seen)
            eff = _max(eff, e2)
            why += w2[:2]
        for m2 in d.mods:
            e2, w2 = of_mod(m2[0], m2[1], set())
            if RANK[e2] > RANK["computes"]:
                eff = _max(eff, e2)
                why += w2[:1]
        memo[name] = (eff, why)
        return eff, why

    vmemo = {}

    def of_verb(verb, seen):
        if verb in vmemo:
            return vmemo[verb]
        body = branches.get(verb)
        if body is None:
            body = next((b for k, b in branches.items() if k.endswith("*") and verb.startswith(k[:-1])), None)
        if body is None:
            # a verb with no branch of its own falls to a method of the same name
            r = of_method(verb, seen) if verb in direct else ("computes", [])
            vmemo[verb] = r
            return r
        al, fr = bctx.get(verb, (a_alias, a_from))
        v = _Direct(al, fr)
        for stmt in body:
            v.visit(stmt)
        # `getattr(self, task)(...)` dispatches to the method NAMED BY THE VERB;
        # missing it read record_plant, log_dry_start and 19 others as computes.
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
               and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) and n.args[0].id == "self"
               and isinstance(n.args[1], ast.Name) and n.args[1].id == "task"
               for stmt in body for n in ast.walk(stmt)):
            v.calls.add(verb)
        eff, why = v.effect, [f"{verb} branch: {w}" for w in v.why]
        for c in v.calls:
            if c == "handle_task":
                continue
            e2, w2 = of_method(c, seen | {f"verb:{verb}"})
            if RANK[e2] > RANK["computes"]:
                eff = _max(eff, e2)
                why += w2[:2]
        for t in v.tasks:
            if f"verb:{t}" not in seen:
                e2, w2 = of_verb(t, seen | {f"verb:{verb}"})
                eff = _max(eff, e2)
                why += w2[:2]
        for m2 in v.mods:
            e2, w2 = of_mod(m2[0], m2[1], set())
            if RANK[e2] > RANK["computes"]:
                eff = _max(eff, e2)
                why += w2[:1]
        vmemo[verb] = (eff, why)
        return eff, why

    verbs = {k for k in branches if not k.endswith("*")}
    return {v: {"effect": of_verb(v, set())[0], "why": of_verb(v, set())[1][:4]} for v in sorted(verbs)}, of_verb
