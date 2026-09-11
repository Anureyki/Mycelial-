#!/usr/bin/env python3
"""The narrow channel. A closed verb set, and the only way in or out.

    ch = Channel("trust_agent", backend)
    ch.call("fs.read", {"path": "instrument.txt"})

WHY NARROW IS THE WHOLE DESIGN. The agent loop stays outside the sandbox and
the side effects stay inside. What crosses is a command from a fixed list, and
the list is short on purpose: every verb here is a hole in the boundary, so the
question for each is not "is this useful" but "is this worth a hole".

A CLOSED SET, NOT A FILTER. `exec` with an arbitrary string is not a narrow
channel with validation - it is a shell, and a shell is every verb at once. So
`proc.exec` takes an argv LIST against an allowlisted binary, never a command
line, because a command line is a parser waiting to be escaped.

The verbs, and what each one costs:

    fs.read     read one path inside the sandbox
    fs.write    write one path inside the sandbox
    fs.list     enumerate a directory inside the sandbox
    proc.exec   run an ALLOWLISTED binary with an argv list
    net.fetch   an HTTP GET to an allowlisted host
    browser.*   navigate, read, click - a profile that is this agent's alone

WHAT IS DELIBERATELY ABSENT, each for a stated reason:

    no shell        a shell is unbounded; every other verb becomes reachable
    no fs.delete    destruction across a boundary needs a person, not a verb
    no env.get      credentials are INJECTED at launch by
                    core/sandbox/credentials.py, never fetched on demand. A
                    verb that reads the environment turns the credential
                    manifest into a suggestion.
    no net.post     sending to a third party is refused structurally in this
                    system already. A sandbox verb that could POST would be a
                    way around that, reached from inside a VM where the veto
                    cannot see it.

THE VETO AND PROVENANCE SIT HERE, AT THE BOUNDARY, NOT INSIDE. A check that
runs inside the sandbox is a check the sandbox can lie about. Every call is
authorised and recorded on the OUTSIDE, before it crosses.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

VERBS = {
    "fs.read":    {"required": ("path",),          "mutates": False},
    "fs.write":   {"required": ("path", "content"), "mutates": True},
    "fs.list":    {"required": ("path",),          "mutates": False},
    "proc.exec":  {"required": ("argv",),          "mutates": True},
    "net.fetch":  {"required": ("url",),           "mutates": False},
    "browser.goto":  {"required": ("url",),        "mutates": True},
    "browser.read":  {"required": (),              "mutates": False},
    "browser.click": {"required": ("selector",),   "mutates": True},
}

# An argv[0] must be on this list. A binary not named here is not "probably
# fine" - it is a program nobody decided to allow.
DEFAULT_BINARIES = ("python3", "git", "ls", "cat", "grep", "wc", "head", "tail")


class ChannelDenied(PermissionError):
    """Refused at the boundary. Never raised from inside the sandbox."""


class Channel:
    def __init__(self, agent_id, backend, allow_hosts=(), allow_binaries=None,
                 veto=None, provenance=True, log=None):
        self.agent_id = agent_id
        self.backend = backend
        self.allow_hosts = tuple(allow_hosts)
        self.allow_binaries = tuple(allow_binaries or DEFAULT_BINARIES)
        self.veto = veto
        self.provenance = provenance
        self.log = log or (lambda *_a, **_k: None)

    # ---------------- authorisation, outside the sandbox ----------------

    def _authorise(self, verb, payload):
        if verb not in VERBS:
            raise ChannelDenied(
                f"{verb!r} is not a channel verb. The set is closed: "
                f"{sorted(VERBS)}. A verb that is not on the list is not a "
                f"capability that was forgotten, it is one nobody granted.")
        spec = VERBS[verb]
        missing = [k for k in spec["required"] if k not in payload]
        if missing:
            raise ChannelDenied(f"{verb} requires {missing}")

        if verb == "proc.exec":
            argv = payload.get("argv")
            if not isinstance(argv, list) or not argv or \
                    not all(isinstance(x, str) for x in argv):
                raise ChannelDenied(
                    "proc.exec takes an argv LIST of strings. A command string "
                    "would be a shell, and a shell is every verb at once.")
            binary = os.path.basename(argv[0])
            if binary not in self.allow_binaries:
                raise ChannelDenied(
                    f"{binary!r} is not allowlisted for {self.agent_id}. "
                    f"Allowed: {sorted(self.allow_binaries)}")

        if verb in ("net.fetch", "browser.goto"):
            url = str(payload.get("url") or "")
            host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
            if not host:
                raise ChannelDenied("no host in url")
            if not any(host == h or host.endswith("." + h)
                       for h in self.allow_hosts):
                allowed = (sorted(self.allow_hosts) or
                           "NOTHING - this agent has no network allowance")
                raise ChannelDenied(
                    f"{host!r} is not allowlisted for {self.agent_id}. "
                    f"Allowed: {allowed}")

        for k in ("path",):
            p = payload.get(k)
            if p is not None and (os.path.isabs(str(p)) or ".." in str(p).split("/")):
                raise ChannelDenied(
                    f"{k}={p!r} escapes the sandbox root. Paths are relative "
                    f"and may not traverse upward.")

        if self.veto:
            # The veto runs OUT HERE. A veto inside the sandbox is a veto the
            # sandbox can lie about.
            v = self.veto({"agent": self.agent_id, "verb": verb,
                           "payload": payload, "mutates": spec["mutates"]})
            if isinstance(v, dict) and v.get("decision") == "refuse":
                raise ChannelDenied(f"vetoed: {v.get('reason') or v.get('register')}")

    def _record(self, verb, payload, outcome, ms, error=None):
        if not self.provenance:
            return None
        try:
            from core.provenance_schemas import new_provenance_event
            from core.provenance_manager import ProvenanceManager
            import hashlib
            ev = new_provenance_event(
                operation="execute" if VERBS[verb]["mutates"] else "review",
                actor_type="agent", agent_id=self.agent_id,
                metadata={
                    "channel_verb": verb, "outcome": outcome, "ms": ms,
                    "error": error,
                    # The payload is HASHED, not stored. A provenance log that
                    # copies file contents out of a sandbox is a second copy of
                    # whatever the sandbox was isolating.
                    "payload_sha256": hashlib.sha256(
                        json.dumps(payload, sort_keys=True, default=str)
                        .encode()).hexdigest(),
                    "payload_keys": sorted(payload),
                    "boundary": "sandbox_channel",
                })
            ProvenanceManager().record_event(ev)
            return ev["event_id"]
        except Exception as e:
            self.log(f"CHANNEL PROVENANCE NOT WRITTEN {verb}: {e}")
            return None

    def call(self, verb, payload=None):
        """The only entry point. Authorise, cross, record."""
        payload = payload or {}
        t0 = time.time()
        try:
            self._authorise(verb, payload)
        except ChannelDenied as e:
            self._record(verb, payload, "denied",
                         int((time.time() - t0) * 1000), str(e))
            raise
        try:
            result = self.backend.execute(self.agent_id, verb, payload)
            outcome = "ok"
            err = None
        except Exception as e:
            result, outcome, err = None, "error", str(e)[:200]
        ms = int((time.time() - t0) * 1000)
        ev = self._record(verb, payload, outcome, ms, err)
        return {"verb": verb, "agent": self.agent_id, "outcome": outcome,
                "result": result, "error": err, "ms": ms,
                "provenance_event": ev,
                "at": datetime.now(timezone.utc).isoformat()}
