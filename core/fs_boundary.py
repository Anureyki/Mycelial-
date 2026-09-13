#!/usr/bin/env python3
"""Which directories hold what, and what the filesystem must enforce.

    from core.fs_boundary import audit, harden
    audit()       # -> findings, changes nothing
    harden()      # -> applies the declared modes

WHAT THIS LAYER CAN AND CANNOT DO, stated first because confusing them is the
way a system acquires a boundary it does not have:

    POSIX permissions stop OTHER USERS. They are the only thing standing
    between the web server on this box and 267MB of transcripts.

    They do NOT stop other AGENTS. Every agent runs as the same user, so
    grow_agent can open knowledge_base/accounting_agent/statements/ with a
    plain open(). core/acl.py is what refuses that, in code.

    The sandbox (core/sandbox/) is the third layer and the only one that would
    survive a compromised process.

None substitutes for another, and this file is the first only. A directory
mode does not make an agent well-behaved; it makes a different Unix user
unable to read the file at all.

THE STATE THIS WAS FOUND IN, 2026-09-13: every sensitive directory 775 -
group-writable and world-readable - and 2,008 files readable by anyone on the
box. private/, whose whole purpose is the opposite, was 775. state/memory.db
and state/audit.db were world-readable. /home/anureyki being 750 was the only
thing preventing it, which is a protection nobody chose and nobody was
checking.

MODES ARE DECLARED, NOT ASSUMED FROM UMASK. A file created by os.makedirs
inherits whatever umask the process happened to start with - so the same code
produces 0700 under one service manager and 0775 under another, and neither
is visible until somebody looks. Every path below states the mode it requires.
"""
import os
import stat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# dir_mode, file_mode, what lives here, why it matters.
#
# 0o700 / 0o600 wherever the contents are personal, operational, or a
# credential. 0o755 is correct for code and published reference material - a
# repository that is public anyway loses nothing by being readable, and making
# it 700 would only hide which directories actually matter.
BOUNDARIES = {
    "private": (0o700, 0o600,
                "The principal's own financial inventory, account overlay, "
                "counterparties, instruments and extracted evidence. Was 775."),
    "state": (0o700, 0o600,
              "Runtime state including memory.db, audit.db, the webapp "
              "credential file and TLS keys. Was 775 with 2,008 readable "
              "files."),
    "knowledge_base": (0o700, 0o600,
                       "The principal's working documents. The accounting "
                       "statements, instruments and trust_estate directories "
                       "are where real financial documents will land."),
    "datasets": (0o700, 0o600,
                 "Training pairs and the security record store. Personal "
                 "under the data-sensitivity policy."),
    "weights": (0o700, 0o600, "Model checkpoints."),
    "logs": (0o700, 0o600,
             "Agent logs. They carry request content, which is why they are "
             "not world-readable even though they look innocuous."),
    "reports": (0o700, 0o600, "Generated analysis over live data."),
}

# Deliberately NOT hardened, and the reason recorded so it is a decision
# rather than an oversight.
PUBLIC_BY_DESIGN = {
    "reference": "Published statutes, regulations and treatises. The corpus "
                 "is committed to a public repository; hiding it locally "
                 "would only obscure which directories actually matter.",
    "webapp": "Served to the browser by nginx.",
    "core": "Source code, public.",
    "agents": "Source code, public.",
    "tools": "Source code, public.",
    "config": "Configuration, public. Secrets live in .env, which is "
              "gitignored and checked separately.",
}


# Trees that are not ours to re-permission. A virtualenv under state/ is a
# build artifact whose modes belong to pip; walking it produced a crash on a
# symlink and would have been noise even if it had not.
SKIP_DIRS = {"__pycache__", "ci-venv", "venv", ".venv", "node_modules",
             "site-packages"}


def _walk(base):
    for dirpath, dirnames, files in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        out = []
        for f in files:
            full = os.path.join(dirpath, f)
            # SYMLINKS ARE SKIPPED, not followed. chmod() follows a link and
            # changes the TARGET - so hardening a directory could silently
            # re-permission something entirely outside it, which is the
            # opposite of a boundary. A broken link raises instead.
            if os.path.islink(full):
                continue
            out.append(full)
        yield dirpath, out


def audit():
    """-> findings. Reads only; changes nothing.

    Separate from harden() on purpose: a security tool whose only mode is
    'fix it' cannot be used to answer 'is it already right?', and a report
    that is produced by changing the thing it reports on is not a report."""
    findings = []
    for rel, (dmode, fmode, why) in sorted(BOUNDARIES.items()):
        base = os.path.join(ROOT, rel)
        if not os.path.isdir(base):
            findings.append({"path": rel, "issue": "absent",
                             "detail": "declared and not present - not a "
                                       "failure, but the declaration is "
                                       "describing something that is not here"})
            continue
        for dirpath, files in _walk(base):
            got = stat.S_IMODE(os.stat(dirpath).st_mode)
            if got & 0o077:
                findings.append({
                    "path": os.path.relpath(dirpath, ROOT), "kind": "directory",
                    "mode": oct(got), "required": oct(dmode),
                    "issue": "readable or writable outside the owner",
                    "why_it_matters": why})
            for f in files:
                try:
                    g = stat.S_IMODE(os.stat(f).st_mode)
                except OSError:
                    continue
                if g & 0o077:
                    findings.append({
                        "path": os.path.relpath(f, ROOT), "kind": "file",
                        "mode": oct(g), "required": oct(fmode),
                        "issue": "readable or writable outside the owner",
                        "why_it_matters": why})
    return findings


def harden(dry_run=False):
    """Apply the declared modes. -> what changed."""
    changed = []
    for rel, (dmode, fmode, _why) in sorted(BOUNDARIES.items()):
        base = os.path.join(ROOT, rel)
        if not os.path.isdir(base):
            continue
        for dirpath, files in _walk(base):
            if stat.S_IMODE(os.stat(dirpath).st_mode) != dmode:
                changed.append((os.path.relpath(dirpath, ROOT), "dir",
                                oct(stat.S_IMODE(os.stat(dirpath).st_mode)),
                                oct(dmode)))
                if not dry_run:
                    os.chmod(dirpath, dmode)
            for f in files:
                try:
                    cur = stat.S_IMODE(os.stat(f).st_mode)
                except OSError:
                    continue
                # Keep the executable bit where it is already set - a script
                # in logs/ is odd, and silently disarming one would be a
                # different bug wearing a security fix's clothes.
                want = fmode | (cur & 0o100)
                if cur != want:
                    changed.append((os.path.relpath(f, ROOT), "file",
                                    oct(cur), oct(want)))
                    if not dry_run:
                        os.chmod(f, want)
    return changed


def ensure_dir(path, mode=0o700, stop_at=None):
    """Create a directory with the mode DECLARED, not the one umask gives.

    os.makedirs applies the process umask, so identical code produces 0700
    under one service manager and 0775 under another - and which one you got
    is invisible until somebody audits it. Every private store creation path
    goes through here."""
    os.makedirs(path, mode=mode, exist_ok=True)
    # TWO THINGS makedirs DOES NOT DO, both of which bit this repository.
    #
    # 1. It only applies `mode` when it CREATES. An existing directory keeps
    #    whatever it had - which is exactly how 0775 survived everywhere.
    #
    # 2. IT DOES NOT APPLY `mode` TO INTERMEDIATE DIRECTORIES. CPython's
    #    makedirs recurses for the parent WITHOUT passing mode, so they are
    #    created at 0o777 & ~umask. os.makedirs("state/agent_keys/<agent>",
    #    mode=0o700) gives the leaf 0700 and leaves state/agent_keys at 0755.
    #    That is a documented behaviour and it is not obvious from the call
    #    site, which is why it survived three CI runs: locally the parents
    #    already existed at 0700 from an earlier harden, so only a machine
    #    that had never created them could show it.
    #
    # So every level from `stop_at` down is corrected, not just the leaf.
    # THE LEAF ALWAYS, parents only within the tree we own.
    #
    # Bounding the whole walk by ROOT meant a path OUTSIDE the repository -
    # a temp directory, a store somewhere else - had its mode left untouched,
    # because the loop exited before its first iteration. Correcting the leaf
    # is unconditional; walking upward is what has to stop at a boundary, so
    # that hardening one store never re-permissions somebody's home directory.
    if stat.S_IMODE(os.stat(path).st_mode) != mode:
        os.chmod(path, mode)
    root = os.path.abspath(stop_at or ROOT)
    cur = os.path.dirname(os.path.abspath(path))
    while cur.startswith(root) and cur != root:
        try:
            if stat.S_IMODE(os.stat(cur).st_mode) != mode:
                os.chmod(cur, mode)
        except OSError:
            break
        cur = os.path.dirname(cur)
    return path
