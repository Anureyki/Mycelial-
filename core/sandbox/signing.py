#!/usr/bin/env python3
"""Signed artifacts between agents. No shared disk, no unsigned payload.

    art = sign("trust_agent", {"finding": "..."} )
    verify(art)          # -> (ok, why)

WHY SIGNING IS THE POINT OF A SHARED INBOX. Once agents stop sharing a disk,
the inbox is the only path between them - and an inbox anyone can write to is
an inbox anyone can forge from. A message claiming to come from Legal, dropped
into Accounting's inbox, is indistinguishable from a real one unless something
carries the sender's own key.

Ed25519 over a CANONICAL encoding: JSON with sorted keys and no whitespace. Two
processes that serialise the same object differently would produce two
signatures for one artifact and fail verification for no reason, which teaches
people to skip verification.

WHAT IS SIGNED IS WHAT IS READ. The signature covers the body AND the envelope
fields that decide how the body is treated - sender, recipient, kind, issued
time. Signing only the body leaves the routing forgeable: the same finding
redirected to another department, or relabelled as a different kind, still
verifies.

KEYS ARE PER AGENT AND PRIVATE TO IT. Under state/agent_keys/<agent>/, mode
0600. In Phase 2 each key lives inside that agent's VM and never leaves it;
here they are separated by file permission, which is weaker and is the reason
the phase exists.

EXPIRY IS NOT OPTIONAL. A signed artifact with no expiry is a credential that
works forever, and a replayed finding is a finding acted on twice.
"""
import base64
import json
import os
import stat
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEYS = os.path.join(ROOT, "state", "agent_keys")
DEFAULT_TTL_SECONDS = 3600


class SignatureInvalid(Exception):
    pass


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _keypair(agent_id):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    from cryptography.hazmat.primitives import serialization
    d = os.path.join(KEYS, agent_id)
    os.makedirs(d, mode=0o700, exist_ok=True)
    priv_path = os.path.join(d, "ed25519.key")
    if not os.path.exists(priv_path):
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption())
        # Written 0600 BEFORE any bytes land in it - creating world-readable
        # and chmod-ing afterwards leaves a window where the key is readable.
        fd = os.open(priv_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
    with open(priv_path, "rb") as fh:
        key = Ed25519PrivateKey.from_private_bytes(fh.read())
    return key


def public_key_b64(agent_id):
    from cryptography.hazmat.primitives import serialization
    pub = _keypair(agent_id).public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return base64.b64encode(pub).decode()


def sign(sender, body, recipient=None, kind="finding", ttl=DEFAULT_TTL_SECONDS):
    now = datetime.now(timezone.utc)
    envelope = {
        "sender": sender, "recipient": recipient, "kind": kind,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "body": body,
    }
    sig = _keypair(sender).sign(_canonical(envelope))
    return {"envelope": envelope,
            "signature": base64.b64encode(sig).decode(),
            "algorithm": "ed25519",
            "public_key": public_key_b64(sender)}


def _observe(decision, sender, reason):
    """Emit; do not record. The harness decides what the record says."""
    try:
        from core.security_events import emit
        emit("artifact_signed" if decision == "accepted" else "artifact_rejected",
             agent=sender, resource="a2a:artifact", action="verify",
             decision=decision, reason=reason)
    except Exception as _e:
        # Observation must never change the DECISION - a harness that is down
        # cannot be allowed to deny a request, nor to allow one. But a silent
        # observer is the failure this project hunts: a removed import made
        # this path dead while the static gate still passed, because the call
        # was still written. So it is swallowed and SAID.
        try:
            import sys as _sys
            print(f"SECURITY EVENT NOT OBSERVED ({_e}) - the decision stood, "
                  f"the record did not", file=_sys.stderr)
        except Exception:
            pass


def verify(artifact, expected_sender=None, now=None, observe=True):
    """-> (ok, why). Never raises on a bad artifact; a caller must be able to
    branch on a forgery without handling an exception."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(artifact, dict) or "envelope" not in artifact:
        return _rejected(locals().get("sender"), "not an artifact", observe)
    env = artifact["envelope"]
    sender = env.get("sender")
    if expected_sender and sender != expected_sender:
        return _rejected(locals().get("sender"), f"sender is {sender!r}, expected {expected_sender!r}", observe)
    try:
        sig = base64.b64decode(artifact.get("signature") or "")
    except Exception:
        return _rejected(locals().get("sender"), "signature is not base64", observe)

    # THE KEY COMES FROM THE KEYSTORE, NOT FROM THE ARTIFACT. Verifying against
    # the public key the message carries proves only that whoever wrote the
    # message also signed it - which every forger can do.
    try:
        pub_b64 = public_key_b64(sender)
    except Exception as e:
        return _rejected(locals().get("sender"), f"no key on file for {sender!r}: {e}", observe)
    if artifact.get("public_key") and artifact["public_key"] != pub_b64:
        return _rejected(locals().get("sender"), ("the artifact carries a different public key than the "
                       "one on file for this sender"), observe)
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(pub_b64)).verify(sig, _canonical(env))
    except Exception:
        return _rejected(locals().get("sender"), "signature does not verify over the envelope", observe)

    exp = env.get("expires_at")
    if exp:
        t = now or datetime.now(timezone.utc)
        try:
            if datetime.fromisoformat(exp) < t:
                return _rejected(locals().get("sender"), f"expired at {exp}", observe)
        except Exception:
            return _rejected(locals().get("sender"), "expires_at is unparseable", observe)
    if observe:
        _observe("accepted", sender, "signature verifies over the envelope")
    return True, "ok"


def _rejected(sender, why, observe=True):
    """Every refusal path reports itself. A forged artifact that is refused
    silently teaches nothing - the refusal IS the training signal."""
    if observe:
        _observe("rejected", sender, why)
    return False, why


def key_permissions_ok():
    """-> [(agent, mode)] for any private key readable by anyone but its owner."""
    bad = []
    if not os.path.isdir(KEYS):
        return bad
    for agent in sorted(os.listdir(KEYS)):
        p = os.path.join(KEYS, agent, "ed25519.key")
        if not os.path.exists(p):
            continue
        mode = stat.S_IMODE(os.stat(p).st_mode)
        if mode & 0o077:
            bad.append((agent, oct(mode)))
    return bad
