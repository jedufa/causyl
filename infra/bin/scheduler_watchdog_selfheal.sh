#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
out_dir="${repo_root}/runtime/reports/scheduler/watchdog"
mkdir -p "${out_dir}"
lock_file="${out_dir}/.selfheal.lock"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
json_out="${out_dir}/selfheal_${ts}.json"
md_out="${out_dir}/selfheal_${ts}.md"

cd "${repo_root}"
exec 9>"${lock_file}"
if ! flock -n 9; then
  echo "[scheduler_watchdog_selfheal] skipped reason=lock_held"
  exit 0
fi

python infra/bin/scheduler_watchdog.py --mode selfheal --json-out "${json_out}" --md-out "${md_out}" | tee "${out_dir}/latest_selfheal_stdout.json"
cp -f "${json_out}" "${out_dir}/latest_selfheal.json"
cp -f "${md_out}" "${out_dir}/latest_selfheal.md"

echo "[scheduler_watchdog_selfheal] json=${json_out}"
echo "[scheduler_watchdog_selfheal] md=${md_out}"
