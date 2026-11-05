#!/usr/bin/env python3
"""The principal's release of a held action - signed on HIS phone, checked here.

    from core.principal_release import enroll, enrolled, verify_release, args_digest

WHY THIS EXISTS (the principal, 2026-11-05). A hold that anything can release is
not a guardrail, it is a queue - and the process that releases it becomes the
new place authority lives. So a release must carry proof that only he can
produce: an ECDSA P-256 signature made by a key generated INSIDE his phone's
browser as non-extractable. The private key never leaves the phone; nothing on
this machine holds it, so nothing on this machine can forge a release.

WHAT A SIGNATURE BINDS. One hold - its id, server, tool and the sha256 of its
exact arguments - and a short validity window. A yes to one held call cannot
release another, and cannot release the same tool with different arguments.
A hold resolves once (Tool Service), so a captured release cannot be replayed.

CHECKED TWICE, INDEPENDENTLY. Security verifies it before it signs a release
envelope, and Tool Service verifies it again before it executes. Today agent
keys are separated only by file permission (core/sandbox/signing.py says so),
so Tool Service could in principle forge Security's envelope - the principal's
signature is the check that does not depend on that.

ENROLMENT IS WRITE-ONCE. The first key enrolled is the key. A second
enrolment is refused; replacing the key means removing the file by hand
(tools/reset_principal_key.py), and every enrolment raises a Security alert and
an email to him with the fingerprint, so a key he did not enrol is a key he
hears about. Trust on first use, with the use announced.

The public key is not secret; it is still kept under state/ (0600), not in the
repo, because it identifies his device.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_PATH = os.path.join(ROOT, "state", "security", "principal_key.json")
MAX_VALIDITY_SECONDS = 15 * 60
CLOCK_SKEW_SECONDS = 120


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def args_digest(args):
    return hashlib.sha256(canonical(args or {}).encode("utf-8")).hexdigest()


def fingerprint(spki_b64):
    h = hashlib.sha256(base64.b64decode(spki_b64)).hexdigest()[:24].upper()
    return " ".join(h[i:i + 4] for i in range(0, len(h), 4))


def enrolled(path=None):
    p = path or KEY_PATH
    try:
        with open(p) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _load_public(spki_b64):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = serialization.load_der_public_key(base64.b64decode(spki_b64))
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise ValueError("the key is not an ECDSA P-256 public key")
    return key


def enroll(spki_b64, label="", path=None):
    """-> {"enrolled": True, "fingerprint"} or {"error"}. Write-once."""
    p = path or KEY_PATH
    try:
        _load_public(spki_b64)
    except Exception as e:
        return {"error": f"not a usable public key: {e}"}
    from core.fs_boundary import ensure_dir
    ensure_dir(os.path.dirname(p), 0o700)
    rec = {"algorithm": "ECDSA-P256-SHA256", "public_key_spki": spki_b64,
           "fingerprint": fingerprint(spki_b64), "label": str(label)[:60],
           "enrolled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        cur = enrolled(p) or {}
        return {"error": ("a key is already enrolled - enrolment is write-once. Replacing it is a "
                          "deliberate act at the server (tools/reset_principal_key.py)."),
                "enrolled_fingerprint": cur.get("fingerprint")}
    with os.fdopen(fd, "w") as fh:
        json.dump(rec, fh)
    return {"enrolled": True, "fingerprint": rec["fingerprint"]}


def _raw_to_der(sig):
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    if len(sig) != 64:
        raise ValueError(f"expected a 64-byte raw P-256 signature, got {len(sig)} bytes")
    return encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))


def verify_release(payload_str, signature_b64, hold, now=None, path=None):
    """-> (ok, why, payload). `payload_str` is verified as the exact bytes the phone
    signed, then parsed - so no re-serialisation can make two encodings disagree.
    `hold` is the held record: id, server, tool, args."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    key_rec = enrolled(path)
    if not key_rec:
        return False, "no principal key is enrolled - nothing can be released", None
    try:
        pub = _load_public(key_rec["public_key_spki"])
        pub.verify(_raw_to_der(base64.b64decode(signature_b64 or "")),
                   str(payload_str).encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    except Exception as e:
        return False, f"the signature does not verify against the enrolled principal key ({type(e).__name__})", None
    try:
        p = json.loads(payload_str)
    except ValueError:
        return False, "the signed payload is not JSON", None
    if p.get("kind") != "hold_release":
        return False, f"signed payload kind is {p.get('kind')!r}, not 'hold_release'", None
    for field, want in (("hold_id", hold.get("id")), ("server", hold.get("server")),
                        ("tool", hold.get("tool")), ("args_sha256", args_digest(hold.get("args")))):
        if p.get(field) != want:
            return False, f"the signature is for a different {field} than this hold", None
    t = now or datetime.now(timezone.utc)
    try:
        iss = datetime.fromisoformat(str(p["issued_at"]).replace("Z", "+00:00"))
        exp = datetime.fromisoformat(str(p["expires_at"]).replace("Z", "+00:00"))
    except Exception:
        return False, "issued_at / expires_at missing or unparseable", None
    if iss > t + timedelta(seconds=CLOCK_SKEW_SECONDS):
        return False, "the release is dated in the future", None
    if exp < t:
        return False, f"the release expired at {p['expires_at']}", None
    if (exp - iss).total_seconds() > MAX_VALIDITY_SECONDS:
        return False, f"the release is valid for longer than {MAX_VALIDITY_SECONDS // 60} minutes", None
    return True, "signed by the enrolled principal key, for this hold, in its window", p
