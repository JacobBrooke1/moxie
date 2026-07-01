#!/usr/bin/env bash
# Moxie installer (Hermes-style). Run from a cloned repo:
#   git clone https://github.com/JacobBrooke1/moxie.git && cd moxie && ./install.sh
#
# Prefers `uv` (the fast Rust-based Python manager Hermes uses); falls back to venv+pip.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
MOXIE_HOME_DIR="${MOXIE_HOME:-$HOME/.moxie}"

echo "🦡  Installing Moxie..."

if command -v uv >/dev/null 2>&1; then
  echo "→ Using uv"
  uv venv "$MOXIE_HOME_DIR/venv" --python 3.11 || uv venv "$MOXIE_HOME_DIR/venv"
  # shellcheck disable=SC1091
  source "$MOXIE_HOME_DIR/venv/bin/activate"
  uv pip install "$REPO_DIR"
else
  echo "→ uv not found; using python3 venv + pip"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Need uv or python3 (3.9+, 3.11 recommended)." >&2
    exit 1
  fi
  PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3,9) else 0)')
  [ "$PY_OK" = "1" ] || { echo "❌ Python 3.9+ required." >&2; exit 1; }
  python3 -m venv "$MOXIE_HOME_DIR/venv"
  # shellcheck disable=SC1091
  source "$MOXIE_HOME_DIR/venv/bin/activate"
  pip install --quiet --upgrade pip
  pip install --quiet "$REPO_DIR"
fi

echo ""
echo "✅ Moxie installed."
echo ""
echo "Next:"
echo "  moxie init      # set up your local ~/.moxie"
echo "  moxie scan      # try it on bundled sample data"
echo "  moxie review    # approve fixes (nothing sends without your yes)"
echo "  moxie doctor    # check your setup"
echo ""
echo "Activate later with:  source $MOXIE_HOME_DIR/venv/bin/activate"
