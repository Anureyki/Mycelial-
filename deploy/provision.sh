#!/usr/bin/env bash
# Take a fresh Debian/Ubuntu machine to a running MycOS. Idempotent.
#
#   bash deploy/provision.sh            # full provision
#   bash deploy/provision.sh --check    # report only, change nothing
#   bash deploy/provision.sh --no-models  # skip the Ollama pulls (5.8 GB)
#
# WHY THIS EXISTS. MycOS ran on exactly one machine, provisioned by hand over
# months, and the only record of how was a Dockerfile that installs a
# container and a systemd unit with a username baked into it. "Build my own
# device" is not a hardware question first - it is the question of whether
# this can be stood up again at all. Until it can, there is one machine and
# no appliance.
#
# WHAT IT REFUSES TO DO. It does not claim success it did not verify: every
# step reports what it found or changed, and the summary at the end is read
# back from the machine rather than accumulated from what the script
# believes it did. It does not install optional kits - those are pulled when
# something needs them (tools/kit.py), which is the whole reason an
# appliance can ship small. And it does not silently size past the hardware:
# if RAM or disk is under what the live system was measured at, it says so
# before it starts rather than after it fails.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_ONLY=0
WITH_MODELS=1
for a in "$@"; do
  case "$a" in
    --check) CHECK_ONLY=1 ;;
    --no-models) WITH_MODELS=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a"; exit 2 ;;
  esac
done

# Measured off the live system 2026-09-16, not guessed. See the Hardware
# track in DEPLOYMENT_PROGRESS.md - these are floors, not targets.
MIN_RAM_GB=8
MIN_DISK_GB=64
REC_RAM_GB=16
REC_DISK_GB=256

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILED=1; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
FAILED=0

step "0. What this machine is"
CORES=$(nproc)
RAM_GB=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo)
DISK_GB=$(df -BG --output=size / | tail -1 | tr -dc '0-9')
FREE_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
echo "  cores=${CORES}  ram=${RAM_GB}G  disk=${DISK_GB}G (${FREE_GB}G free)  user=$(id -un)"
awk -v r="$RAM_GB" -v m="$MIN_RAM_GB" 'BEGIN{exit !(r+0 < m)}' \
  && bad "RAM ${RAM_GB}G is under the ${MIN_RAM_GB}G floor. A local model will not fit beside the agents." \
  || ok "RAM meets the ${MIN_RAM_GB}G floor"
awk -v r="$RAM_GB" -v m="$REC_RAM_GB" 'BEGIN{exit !(r+0 < m)}' \
  && warn "RAM ${RAM_GB}G is under the ${REC_RAM_GB}G recommendation - the live system is RAM-bound at 7.1G" \
  || true
[ "$DISK_GB" -lt "$MIN_DISK_GB" ] \
  && bad "disk ${DISK_GB}G is under the ${MIN_DISK_GB}G floor" \
  || ok "disk meets the ${MIN_DISK_GB}G floor (${REC_DISK_GB}G recommended)"
if [ "$FAILED" = 1 ] && [ "$CHECK_ONLY" = 0 ]; then
  echo
  echo "Refusing to provision under the measured floor. Re-run with --check to"
  echo "see the report without changing anything, or provision a larger machine."
  exit 1
fi

step "1. System packages"
PKGS=(python3 python3-venv python3-dev build-essential git curl jq socat
      ffmpeg portaudio19-dev zstd sqlite3 mosquitto mosquitto-clients)
MISSING=()
for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
if [ ${#MISSING[@]} -eq 0 ]; then
  ok "all ${#PKGS[@]} packages present"
elif [ "$CHECK_ONLY" = 1 ]; then
  warn "would install: ${MISSING[*]}"
else
  echo "  installing: ${MISSING[*]}"
  sudo apt-get update -qq && sudo apt-get install -y -qq "${MISSING[@]}" \
    && ok "installed ${#MISSING[@]} package(s)" || bad "apt-get failed"
fi

step "2. Ollama"
if command -v ollama >/dev/null 2>&1; then
  ok "ollama present ($(ollama --version 2>/dev/null | head -1))"
elif [ "$CHECK_ONLY" = 1 ]; then
  warn "would install ollama from https://ollama.com/install.sh"
else
  curl -fsSL https://ollama.com/install.sh | sh && ok "ollama installed" || bad "ollama install failed"
fi

step "3. Python environment"
if [ -x "$ROOT/venv/bin/python3" ]; then
  ok "venv exists ($("$ROOT/venv/bin/python3" -V 2>&1))"
elif [ "$CHECK_ONLY" = 1 ]; then
  warn "would create $ROOT/venv"
else
  python3 -m venv "$ROOT/venv" && ok "venv created" || bad "venv creation failed"
fi
if [ -x "$ROOT/venv/bin/python3" ] && [ "$CHECK_ONLY" = 0 ]; then
  # torch: the CPU wheel unless a GPU is actually present. The live system
  # carried torch+cu130 with no GPU - 3.4G of CUDA runtime that could not
  # execute. Provisioning a new box should never repeat that by default.
  if command -v nvidia-smi >/dev/null 2>&1; then
    ok "GPU detected - leaving the torch index at its default"
    "$ROOT/venv/bin/pip" install -q -r "$ROOT/requirements.txt" || bad "pip install failed"
  else
    echo "  no GPU: installing torch from the CPU index"
    "$ROOT/venv/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu \
      || warn "CPU torch install failed; falling through to requirements.txt"
    "$ROOT/venv/bin/pip" install -q -r "$ROOT/requirements.txt" || bad "pip install failed"
  fi
  ok "requirements installed"
fi

step "4. Local models"
MODELS=(qwen2.5:1.5b llama3.2:3b deepseek-coder:1.3b nomic-embed-text moondream)
if [ "$WITH_MODELS" = 0 ]; then
  warn "skipped (--no-models). The inference service will have nothing to route to."
elif ! command -v ollama >/dev/null 2>&1; then
  warn "ollama absent; skipping"
elif [ "$CHECK_ONLY" = 1 ]; then
  HAVE=$(ollama list 2>/dev/null | tail -n +2 | wc -l)
  warn "would ensure ${#MODELS[@]} models (~5.8 GB); ${HAVE} present now"
else
  for m in "${MODELS[@]}"; do
    if ollama list 2>/dev/null | grep -q "^${m%%:*}"; then ok "have $m"
    else echo "  pulling $m"; ollama pull "$m" >/dev/null 2>&1 && ok "pulled $m" || warn "pull failed: $m"; fi
  done
fi

step "5. Directories and boundaries"
if [ "$CHECK_ONLY" = 1 ]; then
  warn "would create the private stores and apply 0700/0600 via core/fs_boundary"
else
  for d in logs state datasets weights knowledge_base reports private; do
    mkdir -p "$ROOT/$d"
  done
  if [ -x "$ROOT/venv/bin/python3" ]; then
    (cd "$ROOT" && "$ROOT/venv/bin/python3" -c "
from core.fs_boundary import harden
r = harden(dry_run=False)
print('  fs_boundary:', r if isinstance(r, str) else 'applied')" 2>/dev/null) \
      && ok "private stores created and hardened" \
      || warn "fs_boundary could not run yet - run tools/check_fs_boundary.py after first start"
  fi
fi

step "6. Boot at power-on"
UNIT=/etc/systemd/system/mycelial.service
if [ -f "$UNIT" ]; then
  ok "systemd unit installed"
elif [ "$CHECK_ONLY" = 1 ]; then
  warn "would install $UNIT for user $(id -un), WorkingDirectory=$ROOT"
else
  # Generated, not copied: the unit in deploy/systemd has a username and a
  # home directory baked into it, which is exactly what stops this repo
  # standing up on a second machine.
  sudo tee "$UNIT" >/dev/null <<UNITEOF
[Unit]
Description=Mycelial agent stack
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=$(id -un)
WorkingDirectory=$ROOT
ExecStart=/bin/bash $ROOT/start_all.sh
ExecStop=/usr/bin/pkill -f "python3 .*(-m agents\\\\.|services/[a-z_]+/[a-z_]+\\\\.py)"
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
UNITEOF
  sudo systemctl daemon-reload && sudo systemctl enable mycelial.service >/dev/null 2>&1 \
    && ok "boot unit installed and enabled for $(id -un)" || bad "systemd install failed"
fi

step "7. Optional kits - NOT installed"
if [ -x "$ROOT/venv/bin/python3" ]; then
  (cd "$ROOT" && "$ROOT/venv/bin/python3" tools/kit.py list 2>/dev/null | sed 's/^/  /') || true
fi
echo "  Kits are pulled when something needs them: python3 tools/kit.py install <name>"
echo "  That is why a base image stays small; a verb whose kit is absent says so."

step "Summary - read back from the machine, not from what this script believes"
printf '  %-22s %s\n' "python"      "$("$ROOT/venv/bin/python3" -V 2>&1 || echo MISSING)"
printf '  %-22s %s\n' "ollama"      "$(command -v ollama >/dev/null && ollama --version 2>/dev/null | head -1 || echo MISSING)"
printf '  %-22s %s\n' "models"      "$(ollama list 2>/dev/null | tail -n +2 | wc -l) present"
printf '  %-22s %s\n' "boot unit"   "$(systemctl is-enabled mycelial.service 2>/dev/null || echo 'not installed')"
printf '  %-22s %s\n' "disk free"   "$(df -h / | tail -1 | awk '{print $4" of "$2" ("$5" used)"}')"
echo
if [ "$CHECK_ONLY" = 1 ]; then
  echo "  --check: nothing was changed."
elif [ "$FAILED" = 1 ]; then
  echo "  Provisioning reported failures above. Do not assume the stack will start."
  exit 1
else
  echo "  Start it:   bash start_all.sh      (or: sudo systemctl start mycelial)"
  echo "  Verify it:  bash tools/ci_local.sh"
fi
exit "$FAILED"
