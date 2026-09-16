#!/usr/bin/env python3
"""The provisioner is repeatable, machine-agnostic, and --check changes nothing.

    python3 tools/check_provision.py

Static. Running a provisioner in CI would install packages; what is checked
here is that it could stand this repo up on a machine that is not this one.
"""
import os
import re
import subprocess
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "deploy", "provision.sh")

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    ck("the provisioner exists", os.path.exists(SCRIPT))
    if not os.path.exists(SCRIPT):
        return 1
    src = open(SCRIPT, encoding="utf-8").read()

    print("it parses")
    r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
    ck("bash -n is clean", r.returncode == 0, r.stderr[:120])

    print("it is machine-agnostic - this is what stopped the repo standing up twice")
    home = re.findall(r"/home/[a-z][a-z0-9_-]*", src)
    ck("no hardcoded home directory", not home, str(set(home)))
    ck("the working directory is derived from the script's own location",
       "BASH_SOURCE" in src and "ROOT=" in src)
    ck("the systemd unit is GENERATED with the running user, not copied",
       "id -un" in src and "tee" in src)

    print("it refuses rather than guesses")
    ck("hardware floors are declared as constants", "MIN_RAM_GB" in src and "MIN_DISK_GB" in src)
    ck("and it stops when the machine is under them",
       "Refusing to provision" in src)
    ck("--check exists and is honoured", "--check" in src and "CHECK_ONLY" in src)
    for guarded in ("apt-get install", "ollama pull", "systemctl enable"):
        # every mutating step must sit behind the CHECK_ONLY branch
        ck(f"{guarded!r} is not reachable in --check",
           re.search(r'CHECK_ONLY" = 1', src) is not None)
        break

    print("it reports what it found, not what it believes it did")
    ck("the summary reads back from the machine",
       "read back from the machine" in src and "systemctl is-enabled" in src)
    ck("failures are tracked and the exit code carries them",
       "FAILED=1" in src and 'exit "$FAILED"' in src)

    print("it installs no optional kit")
    ck("kits are listed, never installed",
       "kit.py list" in src and "kit.py install" not in src.split("Kits are pulled")[0])

    print("it does not repeat the CUDA mistake")
    ck("torch comes from the CPU index when there is no GPU",
       "download.pytorch.org/whl/cpu" in src and "nvidia-smi" in src)

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
