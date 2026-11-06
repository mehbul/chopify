#!/usr/bin/env bash
# One-command installer for Chopify on macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/mehbul/chopify/main/install.sh | bash
#
# Environment variables:
#   CHOPIFY_DIR       Install folder (default: ./chopify)
#   CHOPIFY_NO_START  Set to 1 to install without launching Studio
#   CHOPIFY_DRY_RUN   Set to 1 to print the plan without changing anything
set -euo pipefail

REPO_URL="https://github.com/mehbul/chopify.git"
TARGET="${CHOPIFY_DIR:-$PWD/chopify}"
NO_START="${CHOPIFY_NO_START:-0}"
DRY_RUN="${CHOPIFY_DRY_RUN:-0}"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
fail() { printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

if [ "$DRY_RUN" = "1" ]; then
  step "Dry run"
  note "Install folder: $TARGET"
  note "Repository:     $REPO_URL"
  note "Launch Studio:  $([ "$NO_START" = "1" ] && echo no || echo yes)"
  exit 0
fi

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

install_system_package() {
  package="$1"
  if command -v brew >/dev/null 2>&1; then
    brew install "$package"
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y "$package"
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y "$package"
  else
    fail "$package is missing and no supported package manager was found. Install it manually, then rerun."
  fi
}

if ! command -v git >/dev/null 2>&1; then
  step "Installing Git"
  install_system_package git
fi

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  step "Installing Python 3"
  if command -v brew >/dev/null 2>&1; then
    install_system_package python@3.12
  else
    install_system_package python3
  fi
  PYTHON="$(find_python || true)"
fi
[ -n "$PYTHON" ] || fail "Python 3.11 or newer is required."

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  step "Installing ffmpeg"
  install_system_package ffmpeg
fi
command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is required."
command -v ffprobe >/dev/null 2>&1 || fail "ffprobe is required."

if [ -e "$TARGET" ] && [ ! -f "$TARGET/chopify.py" ]; then
  if [ -n "$(ls -A "$TARGET" 2>/dev/null || true)" ]; then
    fail "$TARGET already exists and is not a Chopify checkout. Set CHOPIFY_DIR to another folder and rerun."
  fi
fi

if [ -f "$TARGET/chopify.py" ]; then
  step "Using existing checkout at $TARGET"
else
  step "Cloning Chopify into $TARGET"
  git clone --depth 1 "$REPO_URL" "$TARGET"
fi

if [ ! -x "$TARGET/.venv/bin/python" ]; then
  step "Creating the virtual environment"
  "$PYTHON" -m venv "$TARGET/.venv"
fi

step "Installing Python dependencies"
"$TARGET/.venv/bin/python" -m pip install --upgrade pip
"$TARGET/.venv/bin/python" -m pip install -r "$TARGET/requirements.txt"

printf '\n'
step "Chopify is installed at $TARGET"

if [ "$NO_START" = "1" ]; then
  note "Start Studio: $TARGET/.venv/bin/python $TARGET/dashboard.py"
  exit 0
fi

step "Starting Chopify Studio at http://127.0.0.1:8765"
note "Press Ctrl+C to stop the server."
cd "$TARGET"
exec .venv/bin/python dashboard.py
