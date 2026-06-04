#!/usr/bin/env bash
# Purpose: Run the full local pytest suite in an isolated Python 3.12 environment aligned with CI.
# Behavior:
# - Creates (or reuses) `.venv-test` with the selected Python launcher.
# - Installs test dependencies used by CI.
# - Sets `PYTHONPATH` to repo root and runs `pytest -q` for the full repository test suite.
# Usage:
# - `bash ./scripts/test-local.sh`
# - `bash ./scripts/test-local.sh --recreate-venv`
# - `bash ./scripts/test-local.sh --skip-install`
# - `bash ./scripts/test-local.sh --python python3.12`
# Version: 1.0.0
# Changelog:
# - 1.0.0: Initial Linux/macOS companion script matching test-local.ps1 behavior.
# Inputs/Environment Variables:
# - `--python <cmd>`: optional Python launcher command used to create the venv. Defaults to `python3.12`.
# Outputs/Side Effects:
# - Creates/updates `.venv-test` in repo root.
# - Installs packages into `.venv-test`.
# Prerequisites:
# - Bash
# - Python 3.12 available via the chosen launcher command

set -euo pipefail

recreate_venv=0
skip_install=0
python_cmd="python3.12"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recreate-venv)
      recreate_venv=1
      shift
      ;;
    --skip-install)
      skip_install=1
      shift
      ;;
    --python)
      if [[ $# -lt 2 ]]; then
        echo "--python requires a value" >&2
        exit 2
      fi
      python_cmd="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
venv_dir="${repo_root}/.venv-test"
venv_python="${venv_dir}/bin/python"

echo "Repository root: ${repo_root}"

if [[ ${recreate_venv} -eq 1 && -d "${venv_dir}" ]]; then
  echo "Removing existing venv: ${venv_dir}"
  rm -rf "${venv_dir}"
fi

if [[ ! -x "${venv_python}" ]]; then
  echo "Creating virtual environment with Python launcher: ${python_cmd}"
  # shellcheck disable=SC2206
  python_parts=(${python_cmd})
  "${python_parts[@]}" -m venv "${venv_dir}"
fi

if [[ ! -x "${venv_python}" ]]; then
  echo "Virtual environment creation failed. Ensure Python 3.12 is installed and available via '${python_cmd}'." >&2
  exit 1
fi

if [[ ${skip_install} -eq 0 ]]; then
  echo "Installing test dependencies..."
  "${venv_python}" -m pip install --upgrade pip setuptools wheel
  "${venv_python}" -m pip install pytest pytest-asyncio homeassistant
fi

echo "Running pytest..."
export PYTHONPATH="${repo_root}"
cd "${repo_root}"
"${venv_python}" -m pytest -q
