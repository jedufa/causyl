#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
runtime_dir="${repo_root}/.tooling/python-runtime"
python_bin="${runtime_dir}/bin/python"

mkdir -p "${repo_root}/.tooling"

if [[ ! -x "${python_bin}" ]]; then
  echo "[google_genai_setup] creating venv: ${runtime_dir}"
  python3 -m venv "${runtime_dir}"
fi

echo "[google_genai_setup] installing google-genai into ${runtime_dir}"
"${python_bin}" -m pip install --upgrade pip
"${python_bin}" -m pip install --upgrade google-genai

"${python_bin}" - <<'PY'
import sys
from google import genai
print("[google_genai_setup] python:", sys.executable)
print("[google_genai_setup] google.genai import: ok")
PY

echo "[google_genai_setup] done"
echo "[google_genai_setup] server wrappers will now prefer: ${python_bin}"
