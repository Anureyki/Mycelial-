#!/usr/bin/env python3
"""No commit may be created from staged content carrying sensitive material.

    python3 tools/check_staging_boundary.py

THREE BOUNDARIES, AND NONE SUBSTITUTES FOR ANOTHER:

    .gitignore          stops ORDINARY accidental tracking. It is a
                        convenience, not a control - `git add -f` walks
                        straight through it and is supposed to.
    the pre-commit gate stops sensitive STAGED CONTENT from becoming a
                        commit. This is the enforcement boundary.
    history verification finds exposure that already happened.

The failure this proves against is specific and was untested until now: a
person who knows the file is ignored, decides they know better, and forces it.
That path bypasses .gitignore entirely. If the gate reads the filesystem or
the ignore rules rather than the INDEX, it sees nothing and the commit lands.

IT RUNS IN A THROWAWAY REPOSITORY. The test creates commits, forces adds and
checks that commits do NOT exist - none of which may happen in the real repo
or in CI's checkout. Everything below happens in a temporary git repo seeded
with the real hook and the real scanner.

EVERY FIXTURE IS SYNTHETIC. No real identifier, account, credential or
document appears here. The scanner is pattern-based precisely so that testing
it never requires the thing it protects.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = []

SYNTHETIC_SECRET = "SSN 123-45-6789"          # documented example, never real
CLEAN = "# just a note about the weather\n"


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def git(repo, *args, check=False):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr[:200]}")
    return r


def build_repo():
    """A real git repo with the real hook and the real scanner."""
    d = tempfile.mkdtemp(prefix="staging-boundary-")
    git(d, "init", "-q", check=True)
    git(d, "config", "user.email", "test@example.com", check=True)
    git(d, "config", "user.name", "Boundary Test", check=True)
    os.makedirs(os.path.join(d, "tools"))
    os.makedirs(os.path.join(d, "core"))
    for f in ("tools/check_no_secrets.py", "core/identifier_scan.py"):
        os.makedirs(os.path.dirname(os.path.join(d, f)), exist_ok=True)
        shutil.copy(os.path.join(ROOT, f), os.path.join(d, f))
    os.makedirs(os.path.join(d, ".githooks"))
    shutil.copy(os.path.join(ROOT, ".githooks", "pre-commit"),
                os.path.join(d, ".githooks", "pre-commit"))
    os.chmod(os.path.join(d, ".githooks", "pre-commit"), 0o755)
    git(d, "config", "core.hooksPath", ".githooks", check=True)
    with open(os.path.join(d, ".gitignore"), "w") as fh:
        fh.write("ignored/\n")
    open(os.path.join(d, "README.md"), "w").write("# fixture\n")
    git(d, "add", ".gitignore", "README.md", "tools", "core", ".githooks",
        check=True)
    git(d, "-c", "core.hooksPath=", "commit", "-q", "-m", "seed", check=True)
    return d


def commits(repo):
    r = git(repo, "rev-list", "--count", "HEAD")
    return int(r.stdout.strip() or 0)


def main():
    repo = build_repo()
    base = commits(repo)
    print(f"\n  fixture repository seeded, {base} commit(s)")

    print("\n  1. .gitignore stops an ORDINARY add - and that is all it does")
    os.makedirs(os.path.join(repo, "ignored", "private"), exist_ok=True)
    secret = os.path.join(repo, "ignored", "private", "test_secret.txt")
    open(secret, "w").write(SYNTHETIC_SECRET + "\n")
    git(repo, "add", "ignored/private/test_secret.txt")
    staged = git(repo, "diff", "--cached", "--name-only").stdout
    ck("an ordinary add of an ignored path stages nothing",
       "test_secret" not in staged, staged.strip()[:40] or "index empty")

    print("\n  2. git add -f WALKS THROUGH .gitignore, as it is meant to")
    git(repo, "add", "-f", "ignored/private/test_secret.txt", check=True)
    staged = git(repo, "diff", "--cached", "--name-only").stdout
    ck("the forced path IS staged", "test_secret" in staged,
       "which is why .gitignore is not the security boundary")

    print("\n  3. the gate reads the INDEX, not the filesystem")
    r = subprocess.run([sys.executable, os.path.join(repo, "tools",
                                                     "check_no_secrets.py"),
                        "--staged"], cwd=repo, capture_output=True, text=True)
    ck("the staged-content scan refuses", r.returncode == 1,
       f"exit {r.returncode}")
    ck("and it names the finding without printing the value",
       "test_secret" in (r.stdout + r.stderr)
       and "123-45-6789" not in (r.stdout + r.stderr),
       "a scanner that echoes what it caught has moved it into the log")

    print("\n  4. NO COMMIT IS CREATED - the property is about the commit")
    before = commits(repo)
    r = git(repo, "commit", "-m", "forced sensitive fixture")
    after = commits(repo)
    ck("the commit is refused", r.returncode != 0, f"exit {r.returncode}")
    ck("and the commit count did not move", after == before,
       f"{before} -> {after}")

    print("\n  5. a clean fixture passes the same path and DOES commit")
    git(repo, "reset", "-q")
    open(os.path.join(repo, "notes.md"), "w").write(CLEAN)
    git(repo, "add", "notes.md", check=True)
    before = commits(repo)
    r = git(repo, "commit", "-m", "clean fixture")
    after = commits(repo)
    ck("a clean staged change commits normally", r.returncode == 0,
       r.stderr.strip()[:60])
    ck("and the commit count advanced by one", after == before + 1,
       f"{before} -> {after}")

    print("\n  6. remediation is judged on the INDEX, not the working tree")
    # A previously tracked sensitive fixture, then fixed. The working tree can
    # look clean while the index still carries the bad content - so the gate
    # must read `git diff --cached` and not os.walk.
    tracked = os.path.join(repo, "tracked_note.md")
    open(tracked, "w").write(SYNTHETIC_SECRET + "\n")
    git(repo, "add", "-f", "tracked_note.md", check=True)
    open(tracked, "w").write(CLEAN)          # working tree now clean...
    r = subprocess.run([sys.executable, os.path.join(repo, "tools",
                                                     "check_no_secrets.py"),
                        "--staged"], cwd=repo, capture_output=True, text=True)
    ck("a clean working tree with a dirty INDEX is still refused",
       r.returncode == 1,
       "the index is what becomes the commit; the file on disk is not")
    git(repo, "add", "tracked_note.md", check=True)   # restage the fix
    r = subprocess.run([sys.executable, os.path.join(repo, "tools",
                                                     "check_no_secrets.py"),
                        "--staged"], cwd=repo, capture_output=True, text=True)
    ck("once the fix is staged, it passes", r.returncode == 0,
       f"exit {r.returncode}")
    before = commits(repo)
    r = git(repo, "commit", "-m", "remediated")
    ck("and the remediated change commits", r.returncode == 0
       and commits(repo) == before + 1)

    print("\n  7. a scanner failure BLOCKS - it never becomes a clean scan")
    broken = os.path.join(repo, "tools", "check_no_secrets.py")
    shutil.copy(broken, broken + ".bak")
    open(broken, "w").write("import sys\nraise RuntimeError('scanner down')\n")
    open(os.path.join(repo, "another.md"), "w").write(CLEAN)
    git(repo, "add", "another.md", check=True)
    before = commits(repo)
    r = git(repo, "commit", "-m", "should be blocked by a broken scanner")
    ck("a crashing scanner refuses the commit", r.returncode != 0,
       f"exit {r.returncode}")
    ck("and no commit was created", commits(repo) == before)
    shutil.move(broken + ".bak", broken)

    shutil.rmtree(repo, ignore_errors=True)
    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the staging boundary holds, including against git add -f")
    return 0


if __name__ == "__main__":
    sys.exit(main())
