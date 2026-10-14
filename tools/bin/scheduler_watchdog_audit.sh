#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
out_dir="${repo_root}/runtime/reports/scheduler/watchdog"
mkdir -p "${out_dir}"
lock_file="${out_dir}/.audit.lock"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
json_out="${out_dir}/audit_${ts}.json"
md_out="${out_dir}/audit_${ts}.md"

cd "${repo_root}"
exec 9>"${lock_file}"
if ! flock -n 9; then
  echo "[scheduler_watchdog_audit] skipped reason=lock_held"
  exit 0
fi

python infra/bin/scheduler_watchdog.py --mode audit --json-out "${json_out}" --md-out "${md_out}" | tee "${out_dir}/latest_audit_stdout.json"
cp -f "${json_out}" "${out_dir}/latest_audit.json"
cp -f "${md_out}" "${out_dir}/latest_audit.md"

echo "[scheduler_watchdog_audit] json=${json_out}"
echo "[scheduler_watchdog_audit] md=${md_out}"
