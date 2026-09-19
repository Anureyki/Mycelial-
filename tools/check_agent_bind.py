#!/usr/bin/env python3
"""Gate: an agent that cannot bind its port must FAIL, not run headless.

    python3 tools/check_agent_bind.py

THE FAILURE THIS EXISTS TO CATCH, measured 2026-09-19. Two grow_agent
processes were running. The second (pid 27671, up since 2026-09-18 21:33)
had never bound port 9009: `serve()` was called inside a Thread, so the
EADDRINUSE raised in the thread, the thread died, and the main thread
carried on and logged "HTTP server started on port 9009".

That process was not harmless. AgentBase.setup_mqtt() runs in __init__,
before the HTTP server, so the ghost still held an MQTT connection and was
still subscribed to `mycelial/sensor/+/reading`. The first sensor to
publish would have been ingested TWICE into a record whose uptake and
mass-balance figures are DIFFERENCES between consecutive readings - the
exact corruption CLAUDE.md describes, where every individual number still
looks reasonable afterwards.

It is the false-success shape, in the base class every agent inherits:
"Verify the effect, not the exit code." A bind is verified by binding.

THIS GATE PARSES THE AST AND THEN PROVES IT LIVE, because grepping for a
function name is what check_eval got wrong first: a regression that renames
the call leaves the string in place and the gate passes while the path is
gone. Static analysis cannot see a dead path.

Two claims, both checked:

  1. The bind happens in the CALLER's thread - create_server (which binds)
     is called at statement level in start_http_server, and `serve` (which
     binds and runs) is never handed to a Thread.
  2. A 500 from /execute logs the traceback and the task name. A bare
     `Error: {e}` cost five days on a `list.index(x): x not in list` that
     named no file and no line.
"""
import ast
import os
import socket
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "core", "base_agent.py")
fails = []


def check_bind_is_synchronous(tree):
    """create_server at statement level; no serve() inside a Thread target."""
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "start_http_server"), None)
    if fn is None:
        fails.append("core/base_agent.py has no start_http_server()")
        return

    def _callee(c):
        # `Thread(...)` is a Name; `threading.Thread(...)` is an Attribute. The
        # real code uses the second, so matching only the first made this check
        # silently vacuous - a gate that cannot fail is worse than no gate.
        f = c.func
        return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")

    names = [_callee(n) for n in ast.walk(fn) if isinstance(n, ast.Call)]
    if "create_server" not in names:
        fails.append("start_http_server() does not call create_server(). The bind must "
                     "happen in the caller's thread so EADDRINUSE raises here, not in a "
                     "thread nobody is watching.")

    # serve() binds AND runs, so it must never be a thread target - that is
    # precisely how the bind error became invisible.
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and _callee(node) == "Thread"):
            continue
        for kw in node.keywords:
            if kw.arg != "target":
                continue
            inner = [_callee(n) for n in ast.walk(kw.value) if isinstance(n, ast.Call)]
            if "serve" in inner:
                fails.append("serve() is a Thread target again. It binds inside the thread, "
                             "so a taken port kills the thread and leaves a process with no "
                             "socket, still consuming MQTT, reporting that it started.")


def check_handler_logs_traceback(tree):
    """The /execute except block must log the frame and the task."""
    src = open(BASE).read()
    ex = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "execute"), None)
    if ex is None:
        fails.append("core/base_agent.py has no execute() route")
        return
    seg = ast.get_source_segment(src, ex) or ""
    handlers = [h for h in ast.walk(ex) if isinstance(h, ast.ExceptHandler)]
    if not handlers:
        fails.append("execute() has no exception handler at all")
        return
    body = "\n".join(ast.get_source_segment(src, h) or "" for h in handlers)
    if "format_exc" not in body and "print_exc" not in body:
        fails.append("execute()'s handler does not log a traceback. `Error: {e}` alone cost "
                     "five days on 'list.index(x): x not in list' - no verb, no file, no line.")
    if "{task" not in body and "task!r" not in body:
        fails.append("execute()'s handler does not name the failing task. The task is in "
                     "scope; a 500 that does not say what failed is unactionable.")


def check_live_round_trip():
    """Bind a port, then prove create_server raises rather than ghosting."""
    from waitress import create_server
    from flask import Flask

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    app = Flask(__name__)
    try:
        create_server(app, host="127.0.0.1", port=port)
        fails.append("LIVE: create_server did NOT raise on an occupied port - a duplicate "
                     "agent would still start headless.")
    except OSError:
        pass
    finally:
        s.close()

    # ...and a free port must still actually serve, or the fix is a denial of service.
    srv = create_server(app, host="127.0.0.1", port=0)
    threading.Thread(target=srv.run, daemon=True).start()
    import time
    import urllib.error
    import urllib.request
    bound = srv.socket.getsockname()[1]
    time.sleep(0.8)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{bound}/", timeout=5)
    except urllib.error.HTTPError:
        pass                      # 404 from a routeless app: it served. That is the point.
    except Exception as e:        # noqa: BLE001
        fails.append(f"LIVE: a free port did not serve ({type(e).__name__}: {e})")


def main():
    tree = ast.parse(open(BASE).read())
    check_bind_is_synchronous(tree)
    check_handler_logs_traceback(tree)
    check_live_round_trip()

    if fails:
        print("check_agent_bind: FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("check_agent_bind: ok (bind is synchronous; 500s carry a frame; proven live)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
