#!/usr/bin/env bash
# crosshair installer
#
# - Verifies Python 3.9+
# - Installs the package in editable mode into a private venv under
#   ~/.cursor/crosshair/venv so nothing pollutes the user's system Python
# - Wires into ~/.cursor/hooks.json via `crosshair install`
#
# Safe to re-run: merges with existing hooks and dedupes.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${HOME}/.cursor/crosshair/venv"
STATE_DIR="${HOME}/.cursor/crosshair"

echo "[crosshair] repo: ${REPO_DIR}"
echo "[crosshair] venv: ${VENV_DIR}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[crosshair] python3 not found; please install Python 3.9+" >&2
  exit 1
fi

PY_VERSION="$(python3 -c 'import sys;print("%d.%d" % sys.version_info[:2])')"
MAJOR="${PY_VERSION%%.*}"
MINOR="${PY_VERSION##*.}"
if [ "${MAJOR}" -lt 3 ] || { [ "${MAJOR}" -eq 3 ] && [ "${MINOR}" -lt 9 ]; }; then
  echo "[crosshair] need Python 3.9+, found ${PY_VERSION}" >&2
  exit 1
fi

mkdir -p "${STATE_DIR}/state" "${STATE_DIR}/logs"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[crosshair] creating venv..."
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip >/dev/null
python -m pip install -e "${REPO_DIR}" >/dev/null

VENV_PY="${VENV_DIR}/bin/python"

echo "[crosshair] installing Cursor hooks..."
"${VENV_PY}" -m crosshair install --python "${VENV_PY}" "$@"

VENV_BIN="${VENV_DIR}/bin"

echo
echo "[crosshair] done."
echo "  status:   ${VENV_PY} -m crosshair status"
echo "  analyze:  ${VENV_PY} -m crosshair analyze"
echo "  handoff:  ${VENV_PY} -m crosshair handoff"
echo "  rtk list: ${VENV_BIN}/rtk list"
echo "  rtk gain: ${VENV_BIN}/rtk gain"
echo
echo "  The preToolUse hook rewrites shell commands to 'rtk <cmd>', so make sure"
echo "  ${VENV_BIN} is on Cursor's PATH (or symlink ${VENV_BIN}/rtk into ~/.local/bin)."
echo
echo "  Restart Cursor (or open a new composer) for the hooks to take effect."
